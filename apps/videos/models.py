import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse

from apps.core.languages import DEFAULT_STUDY_LANGUAGE, DEFAULT_TRANSLATION_LANGUAGE, LANGUAGE_CHOICES


def media_dir(media):
    """Pasta de um vídeo no armazenamento: videos/<dono>/<id>/ (todos os arquivos dele ficam ali)."""
    return f"videos/{media.owner_id}/{media.pk}"


def _original_path(media, filename):
    return f"{media_dir(media)}/original{media.extension}"


def _audio_path(media, filename):
    # Referenciada pela migração inicial (campos de áudio completo, já removidos).
    return f"{media_dir(media)}/{filename}"


def _segment_path(segment, filename):
    return f"{media_dir(segment.media)}/segments/{filename}"


class MediaSource(models.Model):
    """Vídeo enviado pelo usuário e o resultado do processamento (áudios, segmentos, legendas)."""

    class Status(models.TextChoices):
        UPLOADING = "uploading", "Enviando"
        QUEUED = "queued", "Na fila"
        PROCESSING = "processing", "Processando"
        READY = "ready", "Pronto"
        FAILED = "failed", "Falhou"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="media_sources")
    title = models.CharField("título", max_length=200)
    language = models.CharField(
        "idioma falado", max_length=10, choices=LANGUAGE_CHOICES, default=DEFAULT_STUDY_LANGUAGE
    )
    target_language = models.CharField(
        "idioma de tradução", max_length=10, choices=LANGUAGE_CHOICES, default=DEFAULT_TRANSLATION_LANGUAGE
    )
    original_filename = models.CharField("arquivo enviado", max_length=255)
    extension = models.CharField(max_length=10)
    size_bytes = models.BigIntegerField("tamanho (bytes)")
    received_bytes = models.BigIntegerField("bytes recebidos", default=0)
    # Só existe até o processamento terminar: depois ficam apenas os segmentos (áudio tratado).
    original = models.FileField("vídeo original", upload_to=_original_path, max_length=255, blank=True)
    duration_seconds = models.FloatField("duração (s)", null=True, blank=True)
    status = models.CharField("situação", max_length=12, choices=Status.choices, default=Status.UPLOADING)
    current_step = models.CharField("etapa atual", max_length=20, blank=True)
    progress = models.PositiveSmallIntegerField("progresso (%)", default=0)
    error_message = models.TextField("erro", blank=True)
    # Sinal de vida do worker: processamentos sem sinal por muito tempo voltam para a fila.
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField("enviado em", auto_now_add=True)
    updated_at = models.DateTimeField("atualizado em", auto_now=True)
    processed_at = models.DateTimeField("processado em", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"]), models.Index(fields=["owner", "created_at"])]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("videos:media_detail", args=[self.pk])

    @property
    def upload_percent(self):
        return int(100 * self.received_bytes / self.size_bytes) if self.size_bytes else 0

    @property
    def has_treated_audio(self):
        """Há áudio tratado (os MP3 dos segmentos) para recortar trechos."""
        return self.segments.exclude(audio="").exists()

    @property
    def is_busy(self):
        return self.status in (self.Status.QUEUED, self.Status.PROCESSING)

    @property
    def current_step_label(self):
        return ProcessingStep.Name(self.current_step).label if self.current_step else ""


class ProcessingStep(models.Model):
    """Histórico de cada etapa do processamento de um vídeo (uma linha por etapa)."""

    class Name(models.TextChoices):
        PROBE = "probe", "Validação do vídeo"
        EXTRACT = "extract", "Extração do áudio"
        CLEAN = "clean", "Redução de ruído e música"
        SEGMENT = "segment", "Divisão em segmentos"
        TRANSCRIBE = "transcribe", "Transcrição (legendas)"

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        RUNNING = "running", "Em andamento"
        DONE = "done", "Concluída"
        FAILED = "failed", "Falhou"

    media = models.ForeignKey(MediaSource, on_delete=models.CASCADE, related_name="steps")
    name = models.CharField(max_length=20, choices=Name.choices)
    order = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField("tentativas", default=0)
    progress = models.PositiveSmallIntegerField(default=0)
    message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["media", "order"]
        constraints = [models.UniqueConstraint(fields=["media", "name"], name="unique_step_per_media")]

    def __str__(self):
        return f"{self.media_id}:{self.name}"

    @property
    def duration_seconds(self):
        if self.started_at and self.finished_at:
            return int((self.finished_at - self.started_at).total_seconds())
        return None


class MediaSegment(models.Model):
    """Trecho de até AUDIO_SEGMENT_SECONDS do áudio tratado, com tempos absolutos no vídeo."""

    class TranscriptStatus(models.TextChoices):
        PENDING = "pending", "Pendente"
        DONE = "done", "Transcrito"
        FAILED = "failed", "Falhou"

    media = models.ForeignKey(MediaSource, on_delete=models.CASCADE, related_name="segments")
    uid = models.UUIDField("identificador", default=uuid.uuid4, unique=True, editable=False)
    index = models.PositiveIntegerField("número")
    start_seconds = models.FloatField("início (s)")
    end_seconds = models.FloatField("término (s)")
    audio = models.FileField(upload_to=_segment_path, max_length=255, blank=True)
    transcript_status = models.CharField(
        max_length=10, choices=TranscriptStatus.choices, default=TranscriptStatus.PENDING
    )
    transcript_error = models.TextField(blank=True)

    class Meta:
        ordering = ["media", "index"]
        constraints = [models.UniqueConstraint(fields=["media", "index"], name="unique_segment_index")]

    def __str__(self):
        return f"{self.media_id}#{self.index}"

    def get_absolute_url(self):
        return reverse("videos:segment_detail", args=[self.media_id, self.index])

    @property
    def number(self):
        return self.index + 1

    @property
    def duration_seconds(self):
        return self.end_seconds - self.start_seconds


class TranscriptCue(models.Model):
    """Fala reconhecida (legenda) com tempos absolutos no vídeo; o texto pode ser revisado."""

    segment = models.ForeignKey(MediaSegment, on_delete=models.CASCADE, related_name="cues")
    media = models.ForeignKey(MediaSource, on_delete=models.CASCADE, related_name="cues")
    index = models.PositiveIntegerField("ordem no segmento")
    start_seconds = models.FloatField("início (s)")
    end_seconds = models.FloatField("término (s)")
    text = models.TextField("texto")
    # Texto reconhecido pela transcrição automática, mantido após a revisão manual.
    original_text = models.TextField("texto reconhecido")

    class Meta:
        ordering = ["media", "start_seconds", "index"]
        constraints = [models.UniqueConstraint(fields=["segment", "index"], name="unique_cue_index")]

    def __str__(self):
        return f"{self.start_seconds:.1f}–{self.end_seconds:.1f}: {self.text[:40]}"

    @property
    def edited(self):
        return self.text != self.original_text
