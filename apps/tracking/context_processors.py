from django.conf import settings
from django.urls import reverse
from django.utils import timezone


def access_session(request):
    """Dados para o cronômetro do cabeçalho (sempre calculados a partir do servidor)."""
    access = getattr(request, "access_session", None)
    if access is None or not access.is_active:
        return {}
    return {
        "timer": {
            "started_at_ms": int(access.started_at.timestamp() * 1000),
            "server_now_ms": int(timezone.now().timestamp() * 1000),
            "last_activity_ms": int(access.last_activity_at.timestamp() * 1000),
            "inactivity_timeout": settings.SESSION_INACTIVITY_TIMEOUT_SECONDS,
            "inactivity_warning": settings.SESSION_INACTIVITY_WARNING_SECONDS,
            "heartbeat": settings.SESSION_HEARTBEAT_SECONDS,
            "study_heartbeat": settings.STUDY_HEARTBEAT_SECONDS,
            "heartbeat_url": reverse("tracking:heartbeat"),
            "study_start_url": reverse("tracking:study_start"),
        }
    }
