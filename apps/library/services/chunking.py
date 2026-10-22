"""
Divisão de textos em trechos de no máximo `max_chars` caracteres.

Regras:
- textos até `max_chars` formam um único trecho;
- os cortes preferem fim de frase, depois espaço (limite de palavra) e só
  cortam no meio de uma palavra quando não há alternativa;
- a concatenação de todos os trechos reproduz exatamente o texto original
  (os espaços de separação ficam no fim do trecho anterior).
"""
import re

from django.conf import settings

SENTENCE_END_RE = re.compile(r"[.!?…。！？][\"'”’»)\]]*\s+|\n\s*")
WHITESPACE_RE = re.compile(r"\s+")


def _best_cut(window):
    """Posição de corte dentro de `window` (len == max_chars + 1 quando possível)."""
    limit = len(window) - 1 if len(window) > 1 else len(window)
    sentence_cuts = [m.end() for m in SENTENCE_END_RE.finditer(window) if 0 < m.end() <= limit]
    # Só aceita um corte por frase se não deixar o trecho muito curto.
    if sentence_cuts and sentence_cuts[-1] >= limit * 0.4:
        return sentence_cuts[-1]
    word_cuts = [m.end() for m in WHITESPACE_RE.finditer(window) if 0 < m.end() <= limit]
    if word_cuts:
        return word_cuts[-1]
    return limit


def split_text(content, max_chars=None):
    max_chars = max_chars or settings.TEXT_CHUNK_MAX_CHARS
    if max_chars <= 0:
        raise ValueError("max_chars deve ser positivo")
    if len(content) <= max_chars:
        return [content] if content else []

    chunks = []
    start = 0
    while start < len(content):
        remaining = len(content) - start
        if remaining <= max_chars:
            chunks.append(content[start:])
            break
        # Olha um caractere além do limite: se for espaço, o corte cai num limite de palavra.
        window = content[start : start + max_chars + 1]
        cut = _best_cut(window)
        cut = min(cut, max_chars)
        chunks.append(content[start : start + cut])
        start += cut
    return chunks
