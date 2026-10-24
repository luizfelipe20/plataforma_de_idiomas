"""Regras de negócio dos termos difíceis e dos textos de prática."""
import random

from django.db import transaction
from django.db.models import F

from apps.core.ai import PracticeTextRequest, get_provider
from apps.core.languages import get_language
from apps.library.models import Text
from apps.library.services.texts import save_text

from .models import DifficultTerm

MAX_TERM_CHARS = 200
MAX_CONTEXT_CHARS = 400
MAX_PRACTICE_TERMS = 15
PRACTICE_TAG = "Prática de vocabulário"
PRACTICE_LENGTHS = {"short": ("Curto", 120), "medium": ("Médio", 250), "long": ("Longo", 400)}


def normalize_term(raw):
    """Junta espaços e quebras de linha e remove pontuação solta nas pontas."""
    return " ".join((raw or "").split()).strip(" \t.,;:!?¿¡\"'“”‘’()[]«»")[:MAX_TERM_CHARS]


@transaction.atomic
def mark_term(owner, raw_term, text, context=""):
    """
    Registra um termo difícil. Se o usuário já o marcou (sem diferenciar maiúsculas),
    incrementa o contador e atualiza o contexto. Retorna (termo, criado).
    """
    term = normalize_term(raw_term)
    if not term:
        raise ValueError("Selecione uma palavra ou frase.")
    context = " ".join((context or "").split())[:MAX_CONTEXT_CHARS]
    existing = (
        DifficultTerm.objects.select_for_update()
        .filter(owner=owner, language=text.source_language, term__iexact=term)
        .first()
    )
    if existing is None:
        return DifficultTerm.objects.create(
            owner=owner, term=term, language=text.source_language, context=context, source_text=text
        ), True
    DifficultTerm.objects.filter(pk=existing.pk).update(times_marked=F("times_marked") + 1)
    existing.refresh_from_db()
    if context:
        existing.context = context
    existing.source_text = text
    existing.save(update_fields=["context", "source_text", "updated_at"])
    return existing, False


def terms_for_language(owner, language):
    """Termos usados para destacar o texto: [{term, meaning}] (mais longos primeiro)."""
    terms = DifficultTerm.objects.filter(owner=owner, language=language).values("term", "meaning")
    return sorted(terms, key=lambda t: -len(t["term"]))


def explain_terms(terms, *, provider=None):
    """
    Preenche o significado dos termos com a IA, agrupando por idioma.
    Retorna a quantidade de termos explicados; falhas da IA propagam como AIServiceError.
    """
    provider = provider or get_provider()
    by_language = {}
    for term in terms:
        by_language.setdefault(term.language, []).append(term)
    explained = 0
    for language, group in by_language.items():
        target = _translation_language(group)
        results = provider.explain_terms(
            [(t.term, t.context) for t in group], get_language(language), get_language(target)
        )
        for term, meaning in zip(group, results):
            term.meaning = meaning
            term.save(update_fields=["meaning", "updated_at"])
            explained += 1
    return explained


def _translation_language(terms):
    """Idioma de tradução do texto de origem mais recente (o idioma que o usuário entende)."""
    texts = [t.source_text for t in terms if t.source_text_id]
    if texts:
        return max(texts, key=lambda t: t.updated_at).target_language
    latest = Text.objects.filter(owner=terms[0].owner).order_by("-updated_at").first()
    return latest.target_language if latest else Text._meta.get_field("target_language").default


def missing_terms(terms, content):
    """Termos que não aparecem no conteúdo (sem diferenciar maiúsculas)."""
    lowered = content.lower()
    return [t for t in terms if t.term.lower() not in lowered]


def create_practice_text(owner, terms, *, length="medium", topic="", provider=None):
    """
    Gera com a IA um texto novo com os termos escolhidos e o salva na biblioteca
    (com trechos e tradução), vinculado aos termos. Retorna (texto, termos_ausentes).
    """
    terms = list(terms)
    languages = {t.language for t in terms}
    if not terms:
        raise ValueError("Selecione ao menos um termo.")
    if len(languages) != 1:
        raise ValueError("Selecione termos de um único idioma para gerar o texto.")
    if len(terms) > MAX_PRACTICE_TERMS:
        raise ValueError(f"Selecione no máximo {MAX_PRACTICE_TERMS} termos por texto.")

    language = languages.pop()
    target = _translation_language(terms)
    provider = provider or get_provider()
    generated = provider.generate_practice_text(
        PracticeTextRequest(
            terms=tuple((t.term, t.meaning) for t in terms),
            source_language=get_language(language),
            target_language=get_language(target),
            words=PRACTICE_LENGTHS.get(length, PRACTICE_LENGTHS["medium"])[1],
            topic=" ".join(topic.split())[:200],
            variation_seed=random.randint(1, 10**6),
        )
    )
    text = Text(
        owner=owner,
        title=(generated.title or "Texto de prática")[:200],
        content=generated.content,
        source_language=language,
        target_language=target,
    )
    save_text(text, new_tag_names=PRACTICE_TAG)
    for term in terms:
        term.practice_texts.add(text)
    return text, missing_terms(terms, text.content)

