"""
Áudio (Text-to-Speech) dos trechos, gerado no servidor e guardado em cache.

Cada arquivo é identificado pelo idioma e pelo hash do conteúdo, de modo que
conteúdos idênticos compartilham o mesmo MP3 (trechos do texto e trechos de
perguntas de compreensão auditiva) e uma edição do texto gera áudio novo
apenas para os trechos alterados.
"""
import logging
import os
import tempfile
from pathlib import Path

from django.conf import settings

from apps.core.languages import get_language
from apps.core.utils import content_hash

logger = logging.getLogger(__name__)


class TTSError(Exception):
    pass


def server_tts_enabled():
    return settings.TTS_PROVIDER == "gtts"


def _path_for(content, language_code):
    language = get_language(language_code).server_tts_lang
    return Path(settings.MEDIA_ROOT) / "tts" / language / f"{content_hash(content)}.mp3"


def audio_path(chunk):
    return _path_for(chunk.content, chunk.text.source_language)


def _synthesize_gtts(text, language, destination):
    from gtts import gTTS
    from gtts.tts import gTTSError

    try:
        tts = gTTS(text, lang=language, timeout=settings.TTS_TIMEOUT_SECONDS)
        tts.save(destination)
    except (gTTSError, ValueError, AssertionError) as exc:
        raise TTSError(f"Não foi possível gerar o áudio: {exc}") from exc


def get_audio(content, language_code, synthesize=None):
    """Caminho do MP3 de `content`, gerando-o na primeira solicitação."""
    if not server_tts_enabled():
        raise TTSError("Áudio no servidor desativado (TTS_PROVIDER).")
    if not content.strip():
        raise TTSError("Não há texto para gerar áudio.")
    path = _path_for(content, language_code)
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    language = get_language(language_code).server_tts_lang
    # Escreve num arquivo temporário e renomeia: requisições simultâneas nunca
    # leem um MP3 pela metade.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".part")
    os.close(fd)
    try:
        (synthesize or _synthesize_gtts)(content, language, tmp_name)
        os.replace(tmp_name, path)
    except Exception as exc:
        logger.warning("Falha no TTS (%s): %s", language, exc)
        raise exc if isinstance(exc, TTSError) else TTSError("Não foi possível gerar o áudio.") from exc
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
    return path


def get_chunk_audio(chunk, synthesize=None):
    """MP3 de um trecho do texto."""
    return get_audio(chunk.content, chunk.text.source_language, synthesize=synthesize)
