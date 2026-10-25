"""Criação de textos de estudo a partir de um pedido livre (prompt) para a IA."""
import random

from apps.core.ai import TextGenerationRequest, get_provider
from apps.core.languages import get_language

from ..models import Text
from .texts import save_text

MAX_TEXTS_PER_REQUEST = 5
MAX_PROMPT_CHARS = 2000
# Tamanho aproximado de cada texto: chave -> (rótulo, palavras)
TEXT_LENGTHS = {"short": ("Curto", 120), "medium": ("Médio", 250), "long": ("Longo", 400)}
LEVELS = {
    "": ("Qualquer nível", ""),
    "beginner": ("Iniciante (A1–A2)", "beginner (CEFR A1–A2): short sentences, common vocabulary"),
    "intermediate": ("Intermediário (B1–B2)", "intermediate (CEFR B1–B2)"),
    "advanced": ("Avançado (C1–C2)", "advanced (CEFR C1–C2): rich vocabulary and idioms"),
}


def generate_texts(owner, prompt, *, source_language, target_language, count=1, length="medium", level="",
                   tags=(), new_tag_names="", provider=None):
    """
    Pede à IA `count` textos conforme `prompt` e salva cada um na biblioteca (trechos e
    tradução). Falhas da IA na geração propagam como AIServiceError, sem criar nada;
    falhas na tradução ficam registradas no texto, como no cadastro manual.
    """
    provider = provider or get_provider()
    generated = provider.generate_texts(
        TextGenerationRequest(
            prompt=prompt.strip()[:MAX_PROMPT_CHARS],
            source_language=get_language(source_language),
            target_language=get_language(target_language),
            count=max(1, min(count, MAX_TEXTS_PER_REQUEST)),
            words=TEXT_LENGTHS.get(length, TEXT_LENGTHS["medium"])[1],
            level=LEVELS.get(level, LEVELS[""])[1],
            variation_seed=random.randint(1, 10**6),
        )
    )
    texts = []
    for item in generated.texts:
        text = Text(
            owner=owner,
            title=(" ".join(item.title.split()) or "Texto gerado por IA")[:200],
            content=item.content.replace("\r\n", "\n"),
            source_language=source_language,
            target_language=target_language,
        )
        texts.append(save_text(text, tags=tags, new_tag_names=new_tag_names))
    return texts
