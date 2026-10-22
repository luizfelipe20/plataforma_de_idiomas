from django.conf import settings
from django.db import models
from django.db.models import Q


class AccessSession(models.Model):
    """Período em que o usuário esteve conectado (login → logout/expiração)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Ativa"
        CLOSED = "closed", "Encerrada"

    class EndReason(models.TextChoices):
        LOGOUT = "logout", "Logout"
        INACTIVITY = "inactivity", "Inatividade"
        ABANDONED = "abandoned", "Abandonada (navegador fechado)"
        REPLACED = "replaced", "Substituída por novo login"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="access_sessions")
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    started_at = models.DateTimeField("início")
    ended_at = models.DateTimeField("término", null=True, blank=True)
    last_seen_at = models.DateTimeField("último sinal do navegador")
    last_activity_at = models.DateTimeField("última atividade do usuário")
    active_seconds = models.PositiveIntegerField("tempo ativo (s)", default=0)
    duration_seconds = models.PositiveIntegerField("duração total (s)", null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    end_reason = models.CharField(max_length=20, choices=EndReason.choices, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["user", "status"]), models.Index(fields=["user", "started_at"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(ended_at__isnull=True) | Q(ended_at__gte=models.F("started_at")),
                name="access_session_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.user} — {self.started_at:%d/%m/%Y %H:%M}"

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE


class StudySession(models.Model):
    """Período de estudo efetivo (leitura, áudio ou resolução de quiz)."""

    class Activity(models.TextChoices):
        READING = "reading", "Leitura"
        AUDIO = "audio", "Áudio"
        LISTENING = "listening", "Compreensão auditiva"
        QUIZ = "quiz", "Resolução de quiz"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="study_sessions")
    access_session = models.ForeignKey(
        AccessSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="study_sessions"
    )
    text = models.ForeignKey(
        "library.Text", on_delete=models.SET_NULL, null=True, blank=True, related_name="study_sessions"
    )
    activity = models.CharField(max_length=20, choices=Activity.choices)
    started_at = models.DateTimeField()
    last_beat_at = models.DateTimeField()
    # Momento até o qual o tempo já foi creditado (só avança em heartbeats ativos).
    last_credited_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField("tempo de estudo creditado (s)", default=0)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["user", "started_at"])]

    def __str__(self):
        return f"{self.get_activity_display()} — {self.started_at:%d/%m/%Y %H:%M}"
