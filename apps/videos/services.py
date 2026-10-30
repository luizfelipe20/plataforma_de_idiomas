"""Upload em blocos, legendas e regras de acesso aos vídeos."""
import os
import shutil

from django.conf import settings
from django.db import transaction
from django.db.models import F

from . import pipeline
from .models import MediaSource, media_dir


class UploadError(ValueError):
    """Pedido de upload inválido (mensagem exibida ao usuário)."""


def validate_upload_request(filename, size, duration=None):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in settings.VIDEO_ALLOWED_EXTENSIONS:
        allowed = ", ".join(e.lstrip(".") for e in settings.VIDEO_ALLOWED_EXTENSIONS)
        raise UploadError(f"Formato não suportado. Use: {allowed}.")
    if not isinstance(size, int) or size <= 0:
        raise UploadError("Arquivo vazio ou tamanho inválido.")
    if size > settings.VIDEO_MAX_UPLOAD_BYTES:
        raise UploadError(f"O arquivo passa do limite de {settings.VIDEO_MAX_UPLOAD_BYTES / 1024**3:.0f} GB.")
    # Duração lida pelo navegador: verificação antecipada (o servidor confirma com ffprobe).
    if duration is not None and duration > settings.VIDEO_MAX_DURATION_SECONDS + 1:
        raise UploadError(f"O vídeo passa do limite de {settings.VIDEO_MAX_DURATION_SECONDS / 3600:g} horas.")
    return ext


def start_upload(owner, *, title, filename, size, language, target_language, duration=None):
    ext = validate_upload_request(filename, size, duration)
    return MediaSource.objects.create(
        owner=owner,
        title=(" ".join((title or "").split()) or os.path.splitext(os.path.basename(filename))[0])[:200],
        language=language,
        target_language=target_language,
        original_filename=os.path.basename(filename)[:255],
        extension=ext,
        size_bytes=size,
    )


def partial_path(media):
    return pipeline.local_path(f"{media_dir(media)}/upload.part")


def append_chunk(media, offset, stream, length):
    """
    Grava um bloco do arquivo a partir de `offset`, lendo o corpo da requisição aos poucos.

    O bloco só é aceito se começar exatamente onde o anterior terminou; assim um envio
    interrompido pode ser retomado a partir de `received_bytes`. Retorna o vídeo atualizado.
    """
    with transaction.atomic():
        # Trava o vídeo: blocos enviados em paralelo (ou repetidos) não se misturam no arquivo.
        media = MediaSource.objects.select_for_update().get(pk=media.pk)
        _write_chunk(media, offset, stream, length)
    media.refresh_from_db()
    return media


def _write_chunk(media, offset, stream, length):
    if media.status != MediaSource.Status.UPLOADING:
        raise UploadError("Este vídeo não está aguardando envio.")
    if offset != media.received_bytes:
        raise UploadError(f"Bloco fora de ordem: esperado a partir do byte {media.received_bytes}.")
    if length <= 0 or length > settings.VIDEO_UPLOAD_CHUNK_BYTES or offset + length > media.size_bytes:
        raise UploadError("Tamanho do bloco inválido.")
    path = partial_path(media)
    written = 0
    with open(path, "r+b" if os.path.exists(path) else "wb") as fh:
        fh.seek(offset)
        fh.truncate()  # descarta restos de um bloco anterior que não foi confirmado
        while written < length:
            data = stream.read(min(1024 * 1024, length - written))
            if not data:
                break
            fh.write(data)
            written += len(data)
    if written != length:
        raise UploadError("O bloco chegou incompleto. Tente novamente.")
    MediaSource.objects.filter(pk=media.pk, received_bytes=offset).update(received_bytes=F("received_bytes") + length)
    media.refresh_from_db()
    if media.received_bytes == media.size_bytes:
        finish_upload(media)


def finish_upload(media):
    name = f"{media_dir(media)}/original{media.extension}"
    shutil.move(partial_path(media), pipeline.local_path(name))
    MediaSource.objects.filter(pk=media.pk).update(original=name)
    media.original.name = name
    pipeline.enqueue(media)


def delete_media(media):
    """Remove o vídeo, os registros e todos os arquivos gerados."""
    folder = pipeline.local_path(f"{media_dir(media)}/.keep")
    media.delete()
    shutil.rmtree(os.path.dirname(folder), ignore_errors=True)


# ---------------------------------------------------------------------------
# Legendas
# ---------------------------------------------------------------------------
def _timestamp(seconds, sep="."):
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_vtt(cues, offset=0.0):
    """WebVTT; `offset` é subtraído para sincronizar com o áudio de um segmento."""
    lines = ["WEBVTT", ""]
    for i, cue in enumerate(cues, start=1):
        lines += [str(i), f"{_timestamp(cue.start_seconds - offset)} --> {_timestamp(cue.end_seconds - offset)}", cue.text, ""]
    return "\n".join(lines)


def to_srt(cues, offset=0.0):
    lines = []
    for i, cue in enumerate(cues, start=1):
        lines += [str(i), f"{_timestamp(cue.start_seconds - offset, ',')} --> {_timestamp(cue.end_seconds - offset, ',')}",
                  cue.text, ""]
    return "\n".join(lines)


def transcript_data(media, segments):
    """Transcrição estruturada (JSON) para outros módulos: tempos absolutos e relativos ao segmento."""
    return {
        "media": {"id": media.pk, "title": media.title, "language": media.language,
                  "duration_seconds": media.duration_seconds},
        "segments": [
            {
                "id": str(seg.uid),
                "number": seg.number,
                "start_seconds": seg.start_seconds,
                "end_seconds": seg.end_seconds,
                "status": seg.transcript_status,
                "cues": [
                    {"start_seconds": round(c.start_seconds, 3), "end_seconds": round(c.end_seconds, 3),
                     "relative_start_seconds": round(c.start_seconds - seg.start_seconds, 3),
                     "relative_end_seconds": round(c.end_seconds - seg.start_seconds, 3),
                     "text": c.text, "edited": c.edited}
                    for c in seg.cues.all()
                ],
            }
            for seg in segments
        ],
    }


# ---------------------------------------------------------------------------
# Fontes para o módulo de questões
# ---------------------------------------------------------------------------
# Limite do texto montado a partir das falas (o mesmo do cadastro manual de textos).
MAX_SOURCE_CHARS = 30000
# Pausa entre falas que vira quebra de parágrafo no texto.
PARAGRAPH_GAP_SECONDS = 3.0
QUIZ_PRESETS = {
    # chave: (rótulo, descrição, plano de tipos)
    "listening": ("Compreensão auditiva", "Perguntas sobre trechos do áudio real do vídeo.",
                  {"listening": 3, "comprehension": 2}),
    "reading": ("Interpretação das legendas", "Perguntas sobre o texto transcrito.",
                {"comprehension": 3, "fill_blank": 1, "find_error": 1}),
    "mixed": ("Misto", "Áudio e texto no mesmo quiz.",
              {"listening": 2, "comprehension": 2, "fill_blank": 1, "word_order": 1}),
}


class SourceError(ValueError):
    pass


def parse_time(value):
    """'75', '1:15' ou '1:01:15' -> segundos (float); None se inválido."""
    parts = (value or "").strip().replace(",", ".").split(":")
    if not parts[0] or len(parts) > 3:
        return None
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        return None
    if any(n < 0 for n in numbers) or any(n >= 60 for n in numbers[1:]):
        return None
    seconds = 0.0
    for n in numbers:
        seconds = seconds * 60 + n
    return seconds


def resolve_source(media, spec):
    """
    Interpreta a fonte escolhida: "all" (vídeo inteiro), "segment:<índice>" ou
    "range:<início>-<fim>" (segundos). Retorna (segmento ou None, início, fim, falas).
    """
    from .models import TranscriptCue

    cues = TranscriptCue.objects.filter(media=media).order_by("start_seconds", "index")
    segment, start, end = None, 0.0, media.duration_seconds or 0.0
    kind, _, value = (spec or "all").partition(":")
    if kind == "segment":
        segment = media.segments.filter(index=int(value) if value.isdigit() else -1).first()
        if segment is None:
            raise SourceError("Segmento inválido.")
        start, end = segment.start_seconds, segment.end_seconds
    elif kind == "range":
        try:
            start, end = (float(v) for v in value.split("-", 1))
        except ValueError as exc:
            raise SourceError("Intervalo inválido.") from exc
        if end <= start:
            raise SourceError("O fim do intervalo deve ser depois do início.")
    elif kind != "all":
        raise SourceError("Fonte inválida.")
    selected = [c for c in cues.filter(start_seconds__lt=end, end_seconds__gt=start)]
    if not selected:
        raise SourceError("Não há falas transcritas nesse trecho.")
    return segment, start, end, selected


def cues_to_text(cues):
    parts, previous = [], None
    for cue in cues:
        if previous is not None:
            parts.append("\n\n" if cue.start_seconds - previous.end_seconds >= PARAGRAPH_GAP_SECONDS else " ")
        parts.append(cue.text)
        previous = cue
    return "".join(parts).strip()


def _fmt(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def text_for_source(media, spec):
    """
    Texto da biblioteca com as falas da fonte, vinculado ao vídeo e ao intervalo.
    Reaproveita o texto já criado para a mesma fonte se o conteúdo não mudou
    (assim vários quizzes do mesmo trecho ficam juntos); revisões geram um texto novo.
    """
    from apps.library.models import Text
    from apps.library.services.texts import save_text

    segment, start, end, cues = resolve_source(media, spec)
    content = cues_to_text(cues)
    if len(content) > MAX_SOURCE_CHARS:
        raise SourceError(
            f"A transcrição desse trecho tem {len(content)} caracteres; o limite para um quiz é {MAX_SOURCE_CHARS}. "
            "Escolha um segmento ou um intervalo menor."
        )
    start, end = cues[0].start_seconds, cues[-1].end_seconds
    existing = Text.objects.filter(
        owner=media.owner, source_media=media, source_segment=segment, content=content,
    ).order_by("-created_at").first()
    if existing:
        attach_text_clips(existing)
        return existing, False
    where = f"segmento {segment.number}" if segment else f"{_fmt(start)}–{_fmt(end)}"
    text = Text(
        owner=media.owner,
        title=f"{media.title} · {where}"[:200],
        content=content,
        source_language=media.language,
        target_language=media.target_language,
        source_media=media,
        source_segment=segment,
        source_start=start,
        source_end=end,
    )
    # Sem tradução automática aqui: transcrições longas deixariam a requisição lenta; a
    # página do texto oferece "Traduzir com IA agora".
    save_text(text, new_tag_names="Vídeo", translate=False)
    attach_text_clips(text)
    return text, True


def _norm(value):
    import re

    return re.sub(r"\W+", " ", value.lower()).strip()


def _source_cues(text):
    """Falas do intervalo de origem de um texto vindo de vídeo."""
    from .models import TranscriptCue

    if not text.source_media_id:
        return []
    return list(
        TranscriptCue.objects.filter(
            media_id=text.source_media_id,
            start_seconds__lt=(text.source_end or 1e12) + 0.01,
            end_seconds__gt=(text.source_start or 0) - 0.01,
        ).order_by("start_seconds", "index")
    )


def locate_passage(text, passage):
    """
    Intervalo (início, fim, segmento) do áudio real em que `passage` é falado, procurando
    nas falas do intervalo de origem do texto. Retorna None se não encontrar com segurança.
    """
    import difflib

    from .models import MediaSegment

    if not (text.source_media_id and passage):
        return None
    cues = _source_cues(text)
    target = _norm(passage)
    if not cues or not target:
        return None
    best, best_ratio = None, 0.0
    # Janelas de 1 a 4 falas consecutivas: a primeira que contém o trecho, ou a mais parecida.
    for size in range(1, 5):
        for i in range(len(cues) - size + 1):
            window = cues[i:i + size]
            joined = _norm(" ".join(c.text for c in window))
            if target in joined:
                best, best_ratio = window, 1.0
                break
            ratio = difflib.SequenceMatcher(None, joined, target).ratio()
            if ratio > best_ratio:
                best, best_ratio = window, ratio
        if best_ratio == 1.0:
            break
    if best is None or best_ratio < 0.75:
        return None
    start, end = best[0].start_seconds, best[-1].end_seconds
    segment = MediaSegment.objects.filter(pk=best[0].segment_id).first()
    return start, end, segment


# Folga antes e depois do trecho de uma pergunta, para não cortar o começo ou o fim da fala.
CLIP_PADDING_SECONDS = 0.25


def _treated_clip(media, start, end, name):
    """
    Recorta [start, end] (+ folga) do áudio **tratado** de `media` para `name` no storage,
    a partir dos MP3 dos segmentos (juntando dois quando o trecho cruza a divisa).
    Retorna o caminho, ou None sem áudio tratado: o original, com ruído, nunca é usado.
    """
    import logging

    from . import ffmpeg

    if media is None:
        return None
    start, end = max(0.0, start - CLIP_PADDING_SECONDS), end + CLIP_PADDING_SECONDS
    segments = [s for s in media.segments.order_by("index") if s.end_seconds > start and s.start_seconds < end]
    sources = [pipeline.local_path(s.audio.name) for s in segments if s.audio]
    if not segments or len(sources) != len(segments) or not all(os.path.exists(p) for p in sources):
        return None
    offset = segments[0].start_seconds
    path = pipeline.local_path(name)
    tmp = f"{path}.part.mp3"
    try:
        ffmpeg.cut_clip(sources, tmp, start - offset, end - offset)
        os.replace(tmp, path)
    except (ffmpeg.FFmpegError, OSError):
        logging.getLogger(__name__).warning("Não foi possível recortar %s do áudio tratado", name)
        return None
    return path


def _stored_clip(obj):
    from django.core.files.storage import default_storage

    if obj.audio_clip and default_storage.exists(obj.audio_clip.name):
        return pipeline.local_path(obj.audio_clip.name)
    return None


def _save_clip(obj, media, filename):
    name = obj.audio_clip.field.generate_filename(obj, filename)
    path = _treated_clip(media, obj.audio_start, obj.audio_end, name)
    if path:
        type(obj).objects.filter(pk=obj.pk).update(audio_clip=name)
        obj.audio_clip.name = name
    return path


def question_clip(question):
    """
    Caminho do MP3 com o trecho de uma pergunta de compreensão auditiva, recortado do
    áudio **tratado** do vídeo e guardado na pergunta (`audio_clip`). Depois de gravado,
    não depende mais do vídeo: continua valendo se o vídeo for excluído.
    Retorna None se não houver recorte nem áudio tratado para gerá-lo: o áudio original,
    com ruído, nunca é usado no lugar (a tela avisa que o áudio tratado está indisponível).
    """
    stored = _stored_clip(question)
    if stored or not question.has_real_audio:
        return stored
    return _save_clip(question, question.quiz.text.source_media, f"q{question.pk}.mp3")


def map_chunk_times(text):
    """
    Localiza cada trecho de um texto vindo de vídeo nas falas transcritas e grava o
    intervalo em que ele é falado. Trechos editados que não batem mais com as falas
    ficam sem intervalo (usam TTS).
    """
    from apps.library.models import TextChunk

    joined, spans = "", []
    for cue in _source_cues(text):
        normalized = _norm(cue.text)
        if not normalized:
            continue
        if joined:
            joined += " "
        spans.append((len(joined), len(joined) + len(normalized), cue))
        joined += normalized
    chunks, cursor = list(text.chunks.all()), 0
    for chunk in chunks:
        target = _norm(chunk.content)
        pos = joined.find(target, cursor) if target else -1
        if pos < 0:
            continue
        cursor = pos + len(target)
        # Um trecho que começa ou termina no meio de uma fala inclui a fala inteira.
        hit = [cue for begin, finish, cue in spans if begin < cursor and finish > pos]
        chunk.audio_start, chunk.audio_end = hit[0].start_seconds, hit[-1].end_seconds
    TextChunk.objects.bulk_update(chunks, ["audio_start", "audio_end"])
    return chunks


def chunk_clip(chunk):
    """MP3 do trecho do texto no áudio tratado do vídeo (guardado no trecho), ou None."""
    stored = _stored_clip(chunk)
    if stored:
        return stored
    text = chunk.text
    if not chunk.has_real_audio and text.source_media_id:
        mapped = {c.pk: c for c in map_chunk_times(text)}.get(chunk.pk)
        if mapped:
            chunk.audio_start, chunk.audio_end = mapped.audio_start, mapped.audio_end
    if not chunk.has_real_audio:
        return None
    return _save_clip(chunk, text.source_media, f"c{chunk.pk}.mp3")


def attach_text_clips(text):
    """Localiza e recorta, na criação do texto, o áudio tratado de todos os trechos."""
    chunks = list(text.chunks.select_related("text__source_media"))
    if any(not c.has_real_audio for c in chunks):
        chunks = map_chunk_times(text)
    for chunk in chunks:
        chunk.text = text
        if chunk.has_real_audio:
            chunk_clip(chunk)


def attach_quiz_clips(quiz):
    """
    Gera na criação do quiz os recortes de áudio tratado das perguntas de compreensão
    auditiva. Retorna quantas ficaram sem áudio tratado (nunca usam o áudio original).
    """
    missing = 0
    for question in quiz.questions.filter(audio_start__isnull=False).select_related("quiz__text__source_media"):
        if question_clip(question) is None:
            missing += 1
    return missing
