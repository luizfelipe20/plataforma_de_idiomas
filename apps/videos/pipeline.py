"""
Pipeline de processamento de vídeos e fila de tarefas no banco.

Etapas (ProcessingStep), executadas pelo worker `manage.py process_media`:

    probe → extract → clean → segment → transcribe

Cada etapa registra tentativas, tempos e erro. Uma falha interrompe o vídeo na
etapa (situação "Falhou"); `retry()` devolve o vídeo à fila e o worker continua da
etapa que falhou, sem novo upload. Arquivos intermediários (WAV) ficam na pasta
`work/` do vídeo até a segmentação e são recriados se tiverem sido apagados.

Os arquivos são gravados direto no caminho final do armazenamento local
(`default_storage.path`). Para usar armazenamento de objetos, troque
`local_path()`/`_store()` por download/upload temporário.
"""
import logging
import math
import os
import threading
import time
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from . import asr, ffmpeg
from .models import MediaSegment, MediaSource, ProcessingStep, TranscriptCue, media_dir

logger = logging.getLogger(__name__)

# Peso de cada etapa no progresso geral (a transcrição é a mais demorada).
STEP_WEIGHTS = {"probe": 1, "extract": 9, "clean": 20, "segment": 10, "transcribe": 60}
# Segmento final menor que isso é incorporado ao anterior.
MIN_LAST_SEGMENT_SECONDS = 1.0
# Sinalizado pelo worker ao receber SIGTERM: a etapa interrompida volta para a fila em vez de falhar.
shutdown_requested = threading.Event()


class StepError(Exception):
    """Falha esperada de uma etapa, com mensagem para o usuário."""


def local_path(name):
    path = default_storage.path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def work_path(media, filename):
    return local_path(f"{media_dir(media)}/work/{filename}")


# ---------------------------------------------------------------------------
# Fila
# ---------------------------------------------------------------------------
def create_steps(media):
    for order, name in enumerate(ProcessingStep.Name.values):
        ProcessingStep.objects.get_or_create(media=media, name=name, defaults={"order": order})


def enqueue(media):
    """Coloca o vídeo na fila (após o upload ou para repetir a partir da etapa que falhou)."""
    create_steps(media)
    with transaction.atomic():
        media.steps.exclude(status=ProcessingStep.Status.DONE).update(
            status=ProcessingStep.Status.PENDING, progress=0, message=""
        )
        MediaSource.objects.filter(pk=media.pk).update(
            status=MediaSource.Status.QUEUED, error_message="", heartbeat_at=None, updated_at=timezone.now()
        )
    media.refresh_from_db()
    return media


def retry(media):
    if media.status != MediaSource.Status.FAILED:
        raise StepError("Só é possível repetir o processamento de um vídeo que falhou.")
    return enqueue(media)


def claim_next(now=None):
    """Reserva o próximo vídeo da fila. A atualização condicional impede que dois workers peguem o mesmo."""
    now = now or timezone.now()
    for pk in MediaSource.objects.filter(status=MediaSource.Status.QUEUED).order_by("created_at").values_list("pk", flat=True)[:10]:
        claimed = MediaSource.objects.filter(pk=pk, status=MediaSource.Status.QUEUED).update(
            status=MediaSource.Status.PROCESSING, heartbeat_at=now, updated_at=now
        )
        if claimed:
            return MediaSource.objects.get(pk=pk)
    return None


def recover_stale(now=None):
    """Devolve à fila vídeos cujo worker parou de dar sinal (queda, reinício do container)."""
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.VIDEO_STALE_SECONDS)
    stale = list(MediaSource.objects.filter(status=MediaSource.Status.PROCESSING, heartbeat_at__lt=cutoff))
    for media in stale:
        logger.warning("Vídeo %s sem sinal do worker desde %s: voltando para a fila.", media.pk, media.heartbeat_at)
        media.steps.filter(status=ProcessingStep.Status.RUNNING).update(
            status=ProcessingStep.Status.PENDING, message="Interrompida (worker reiniciado); será retomada."
        )
        MediaSource.objects.filter(pk=media.pk, status=MediaSource.Status.PROCESSING).update(
            status=MediaSource.Status.QUEUED, updated_at=now
        )
    return len(stale)


def requeue_interrupted(media):
    """Devolve à fila um vídeo cujo processamento foi interrompido no meio de uma etapa."""
    media.steps.filter(status=ProcessingStep.Status.RUNNING).update(
        status=ProcessingStep.Status.PENDING, message="Interrompida (worker reiniciado); será retomada."
    )
    MediaSource.objects.filter(pk=media.pk, status=MediaSource.Status.PROCESSING).update(status=MediaSource.Status.QUEUED)


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------
class Progress:
    """Atualiza progresso e sinal de vida no banco, no máximo a cada `interval` segundos."""

    def __init__(self, media, step, interval=2.0):
        self.media, self.step, self.interval = media, step, interval
        self._last = 0.0
        names = list(STEP_WEIGHTS)
        total = sum(STEP_WEIGHTS.values())
        self.base = sum(STEP_WEIGHTS[n] for n in names[: names.index(step.name)]) / total
        self.weight = STEP_WEIGHTS[step.name] / total

    def __call__(self, fraction, force=False):
        now = time.monotonic()
        if not force and now - self._last < self.interval:
            return
        self._last = now
        fraction = max(0.0, min(1.0, fraction))
        ProcessingStep.objects.filter(pk=self.step.pk).update(progress=int(fraction * 100))
        MediaSource.objects.filter(pk=self.media.pk).update(
            progress=int((self.base + self.weight * fraction) * 100),
            heartbeat_at=timezone.now(),
        )

    def part(self, lo, hi):
        """Sub-progresso para uma parte da etapa (ex.: 0–0,7 extração, 0,7–1 MP3)."""
        return lambda f: self(lo + (hi - lo) * f)


def process(media):
    """Executa as etapas pendentes do vídeo. Retorna True se terminou pronto."""
    create_steps(media)
    for step in media.steps.exclude(status=ProcessingStep.Status.DONE).order_by("order"):
        media.refresh_from_db()
        step.status, step.attempts, step.message = ProcessingStep.Status.RUNNING, step.attempts + 1, ""
        step.started_at, step.finished_at = timezone.now(), None
        step.save()
        MediaSource.objects.filter(pk=media.pk).update(current_step=step.name, heartbeat_at=timezone.now())
        progress = Progress(media, step)
        progress(0, force=True)
        try:
            message = STEPS[step.name](media, progress) or ""
        except (StepError, ffmpeg.FFmpegError, asr.ASRError) as exc:
            return _fail(media, step, str(exc))
        except Exception:  # erro inesperado: registra o traceback e falha a etapa
            logger.exception("Erro inesperado na etapa %s do vídeo %s", step.name, media.pk)
            return _fail(media, step, "Erro inesperado no processamento. Tente repetir.")
        progress(1, force=True)
        step.status, step.message, step.progress, step.finished_at = ProcessingStep.Status.DONE, message, 100, timezone.now()
        step.save()
        logger.info("Vídeo %s: etapa %s concluída.", media.pk, step.name)
    MediaSource.objects.filter(pk=media.pk).update(
        status=MediaSource.Status.READY, current_step="", progress=100, processed_at=timezone.now(), error_message=""
    )
    _cleanup_work(media)
    return True


def _fail(media, step, message):
    if shutdown_requested.is_set():
        logger.info("Vídeo %s: etapa %s interrompida pelo desligamento do worker; volta para a fila.", media.pk, step.name)
        step.status, step.message = ProcessingStep.Status.PENDING, "Interrompida (worker reiniciado); será retomada."
        step.save()
        MediaSource.objects.filter(pk=media.pk).update(status=MediaSource.Status.QUEUED)
        return False
    logger.warning("Vídeo %s falhou na etapa %s: %s", media.pk, step.name, message)
    step.status, step.message, step.finished_at = ProcessingStep.Status.FAILED, message, timezone.now()
    step.save()
    MediaSource.objects.filter(pk=media.pk).update(
        status=MediaSource.Status.FAILED, error_message=f"{step.get_name_display()}: {message}"
    )
    return False


def _cleanup_work(media):
    for name in ("extracted.wav", "clean.wav"):
        try:
            os.remove(work_path(media, name))
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Etapas
# ---------------------------------------------------------------------------
def step_probe(media, progress):
    if not media.original:
        raise StepError("O arquivo do vídeo não foi encontrado. Envie o vídeo novamente.")
    info = ffmpeg.probe(local_path(media.original.name))
    if not info.has_audio:
        raise StepError("O vídeo não tem faixa de áudio.")
    if info.duration <= 0:
        raise StepError("Não foi possível ler a duração do vídeo.")
    if info.duration > settings.VIDEO_MAX_DURATION_SECONDS + 1:
        limit = settings.VIDEO_MAX_DURATION_SECONDS / 3600
        raise StepError(f"O vídeo tem {info.duration / 3600:.1f} h; o limite é {limit:g} h.")
    MediaSource.objects.filter(pk=media.pk).update(duration_seconds=info.duration)
    media.duration_seconds = info.duration
    kind = "vídeo" if info.has_video else "somente áudio"
    return f"{kind}, {info.format_name}, {info.duration / 60:.1f} min"


def _ensure_extracted(media, on_progress=None):
    wav = work_path(media, "extracted.wav")
    if not (os.path.exists(wav) and os.path.getsize(wav) > 0):
        ffmpeg.extract_audio(local_path(media.original.name), wav, duration=media.duration_seconds, on_progress=on_progress)
    return wav


def _ensure_clean(media, on_progress=None):
    wav = work_path(media, "clean.wav")
    if not (os.path.exists(wav) and os.path.getsize(wav) > 0):
        ffmpeg.clean_audio(_ensure_extracted(media), wav, duration=media.duration_seconds, on_progress=on_progress)
    return wav


def _store_audio(media, field, filename, wav, on_progress):
    name = f"{media_dir(media)}/{filename}"
    ffmpeg.encode_mp3(wav, local_path(name), duration=media.duration_seconds, on_progress=on_progress)
    MediaSource.objects.filter(pk=media.pk).update(**{field: name})


def step_extract(media, progress):
    wav = _ensure_extracted(media, progress.part(0, 0.6))
    _store_audio(media, "audio_original", "audio-original.mp3", wav, progress.part(0.6, 1))


def step_clean(media, progress):
    wav = _ensure_clean(media, progress.part(0, 0.7))
    _store_audio(media, "audio_clean", "audio-tratado.mp3", wav, progress.part(0.7, 1))
    return "RNNoise + redução FFT + normalização" if ffmpeg.rnnoise_available() else "redução FFT + normalização (sem RNNoise)"


def segment_bounds(duration, length=None):
    """[(início, fim)] de segmentos consecutivos de até `length` segundos cobrindo `duration`."""
    length = length or settings.AUDIO_SEGMENT_SECONDS
    count = max(1, math.ceil(duration / length))
    bounds = [(i * length, min(duration, (i + 1) * length)) for i in range(count)]
    if len(bounds) > 1 and bounds[-1][1] - bounds[-1][0] < MIN_LAST_SEGMENT_SECONDS:
        last = bounds.pop()
        bounds[-1] = (bounds[-1][0], last[1])
    return bounds


def step_segment(media, progress):
    wav = _ensure_clean(media)
    bounds = segment_bounds(media.duration_seconds)
    with transaction.atomic():
        media.segments.all().delete()  # repetição da etapa: recomeça os segmentos
        segments = MediaSegment.objects.bulk_create(
            MediaSegment(media=media, index=i, start_seconds=start, end_seconds=end) for i, (start, end) in enumerate(bounds)
        )
    for segment in segments:
        name = f"{media_dir(media)}/segments/{segment.number:03d}-{segment.uid}.mp3"
        ffmpeg.encode_mp3(wav, local_path(name), start=segment.start_seconds, length=segment.duration_seconds)
        MediaSegment.objects.filter(pk=segment.pk).update(audio=name)
        progress((segment.index + 1) / len(segments))
    _cleanup_work(media)
    return f"{len(segments)} segmento(s) de até {settings.AUDIO_SEGMENT_SECONDS // 60} min"


def transcribe_segment(segment, transcriber=None):
    """Transcreve um segmento e grava as falas com tempos absolutos (substitui as anteriores)."""
    transcriber = transcriber or asr.get_transcriber()
    recognized = transcriber.transcribe(local_path(segment.audio.name), segment.media.language)
    cues = []
    for cue in recognized:
        start = segment.start_seconds + max(0.0, cue.start)
        end = min(segment.end_seconds, segment.start_seconds + max(cue.end, cue.start))
        if cue.text and end > start:
            cues.append(
                TranscriptCue(segment=segment, media_id=segment.media_id, index=len(cues), start_seconds=start,
                              end_seconds=end, text=cue.text, original_text=cue.text)
            )
    with transaction.atomic():
        segment.cues.all().delete()
        TranscriptCue.objects.bulk_create(cues)
        MediaSegment.objects.filter(pk=segment.pk).update(
            transcript_status=MediaSegment.TranscriptStatus.DONE, transcript_error=""
        )
    return len(cues)


def step_transcribe(media, progress):
    transcriber = asr.get_transcriber()
    segments = list(media.segments.order_by("index"))
    pending = [s for s in segments if s.transcript_status != MediaSegment.TranscriptStatus.DONE]
    failed = 0
    if not segments:
        raise StepError("Não há segmentos para transcrever.")
    for done, segment in enumerate(pending, start=1):
        try:
            transcribe_segment(segment, transcriber)
        except (asr.ASRError, ffmpeg.FFmpegError) as exc:
            failed += 1
            logger.warning("Transcrição do segmento %s falhou: %s", segment.pk, exc)
            MediaSegment.objects.filter(pk=segment.pk).update(
                transcript_status=MediaSegment.TranscriptStatus.FAILED, transcript_error=str(exc)[:500]
            )
        progress(done / len(pending), force=True)
    if failed:
        raise StepError(f"{failed} de {len(segments)} segmento(s) não foram transcritos. Repita para tentar de novo.")
    total = TranscriptCue.objects.filter(media=media).count()
    return f"{total} fala(s) reconhecida(s) com {transcriber.name}"


STEPS = {
    "probe": step_probe,
    "extract": step_extract,
    "clean": step_clean,
    "segment": step_segment,
    "transcribe": step_transcribe,
}
