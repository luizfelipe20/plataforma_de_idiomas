"""Regras de negócio de textos: trechos, tags e tradução."""
import logging

from django.db import transaction
from django.utils import timezone

from apps.core.ai import AIServiceError, get_provider
from apps.core.languages import get_language
from apps.core.utils import content_hash

from ..models import Tag, Text, TextChunk
from .chunking import split_text

logger = logging.getLogger(__name__)


def parse_tag_names(raw):
    """'Viagens, trabalho ,  , Viagens' -> ['Viagens', 'trabalho'] (sem duplicatas)."""
    seen, names = set(), []
    for part in (raw or "").split(","):
        name = " ".join(part.split())[:50]
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def get_or_create_tags(owner, names):
    tags = []
    for name in names:
        tag = Tag.objects.filter(owner=owner, name__iexact=name).first()
        if tag is None:
            tag = Tag.objects.create(owner=owner, name=name)
        tags.append(tag)
    return tags


@transaction.atomic
def sync_chunks(text):
    """
    Recria os trechos de `text` a partir do conteúdo atual.

    Traduções de trechos idênticos (mesmo conteúdo e mesmo idioma de destino)
    são reaproveitadas, evitando novas chamadas à IA.
    """
    reusable = {
        (c.content_hash, c.translation_language): (c.translation, c.translation_provider)
        for c in text.chunks.all()
        if c.translation and c.translation_source_hash == c.content_hash
    }
    text.chunks.all().delete()
    chunks = []
    for order, piece in enumerate(split_text(text.content)):
        digest = content_hash(piece)
        cached, cached_provider = reusable.get((digest, text.target_language), ("", ""))
        chunks.append(
            TextChunk(
                text=text,
                order=order,
                content=piece,
                content_hash=digest,
                translation=cached,
                translation_language=text.target_language if cached else "",
                translation_source_hash=digest if cached else "",
                translation_provider=cached_provider,
            )
        )
    TextChunk.objects.bulk_create(chunks)
    return chunks


def _refresh_full_translation(text):
    text.translation = "".join(
        chunk.translation + ("\n\n" if chunk.content.rstrip(" \t").endswith("\n") else " ")
        for chunk in text.chunks.all()
    ).strip()


def translate_text(text, *, force=False, provider=None):
    """
    Traduz os trechos pendentes de `text` e atualiza a situação do processamento.

    Retorna True em caso de sucesso. Falhas do serviço externo não propagam:
    ficam registradas em `status`/`status_message` para nova tentativa.
    """
    chunks = list(text.chunks.all())
    pending = [c for c in chunks if force or c.translation_is_stale]
    if not pending:
        _refresh_full_translation(text)
        text.status = Text.Status.READY
        text.status_message = ""
        text.save(update_fields=["translation", "status", "status_message", "updated_at"])
        return True

    text.status = Text.Status.PROCESSING
    text.save(update_fields=["status", "updated_at"])
    provider = provider or get_provider()
    try:
        results = provider.translate_segments(
            [c.content for c in pending],
            get_language(text.source_language),
            get_language(text.target_language),
        )
    except AIServiceError as exc:
        logger.warning("Falha ao traduzir texto %s: %s", text.pk, exc)
        text.status = Text.Status.ERROR
        text.status_message = str(exc)[:500]
        text.save(update_fields=["status", "status_message", "updated_at"])
        return False
    except Exception:  # erro inesperado do provedor; não derruba a requisição
        logger.exception("Erro inesperado ao traduzir texto %s", text.pk)
        text.status = Text.Status.ERROR
        text.status_message = "Erro inesperado no serviço de tradução."
        text.save(update_fields=["status", "status_message", "updated_at"])
        return False

    with transaction.atomic():
        for chunk, translated in zip(pending, results):
            chunk.translation = translated
            chunk.translation_language = text.target_language
            chunk.translation_source_hash = chunk.content_hash
            chunk.translation_provider = provider.name
            chunk.save(
                update_fields=["translation", "translation_language", "translation_source_hash", "translation_provider"]
            )
        _refresh_full_translation(text)
        text.status = Text.Status.READY
        text.status_message = ""
        text.translated_at = timezone.now()
        text.save(update_fields=["translation", "status", "status_message", "translated_at", "updated_at"])
    return True


def save_text(text, *, tags=(), new_tag_names="", translate=True):
    """Persiste o texto, as tags (incluindo as criadas na hora) e os trechos."""
    content_changed = True
    if text.pk:
        previous = Text.objects.filter(pk=text.pk).values("content", "target_language").first()
        content_changed = previous is None or previous["content"] != text.content
        language_changed = previous is not None and previous["target_language"] != text.target_language
    else:
        language_changed = False

    with transaction.atomic():
        text.save()
        all_tags = list(tags) + get_or_create_tags(text.owner, parse_tag_names(new_tag_names))
        text.tags.set({t.pk: t for t in all_tags}.values())
        if content_changed:
            sync_chunks(text)
        if content_changed or language_changed or text.translation_is_stale:
            text.status = Text.Status.PENDING
            text.save(update_fields=["status", "updated_at"])

    if translate and text.status != Text.Status.READY:
        translate_text(text)
    return text
