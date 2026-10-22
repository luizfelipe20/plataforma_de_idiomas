import json

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.library.models import Text

from . import services
from .models import AccessSession, StudySession


def _payload(request):
    try:
        return json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _session_state(access, now):
    return {
        "status": "active" if access.is_active else "closed",
        "started_at_ms": int(access.started_at.timestamp() * 1000),
        "server_now_ms": int(now.timestamp() * 1000),
        "last_activity_ms": int(access.last_activity_at.timestamp() * 1000),
        "elapsed_seconds": services.elapsed_seconds(access, now),
        "active_seconds": access.active_seconds,
    }


@login_required
@require_POST
def heartbeat(request):
    """Sinal periódico do navegador. `active` indica interação do usuário desde o último sinal."""
    access = request.access_session
    now = timezone.now()
    if access is None:
        return JsonResponse({"status": "missing"}, status=409)
    access = services.touch(access, now, activity=bool(_payload(request).get("active")))
    return JsonResponse(_session_state(access, now))


@login_required
@require_POST
def study_start(request):
    data = _payload(request)
    activity = data.get("activity")
    if activity not in StudySession.Activity.values:
        return JsonResponse({"error": "Atividade inválida."}, status=400)
    text = None
    if data.get("text_id"):
        try:
            text_id = int(data["text_id"])
        except (TypeError, ValueError):
            return JsonResponse({"error": "Texto inválido."}, status=400)
        text = get_object_or_404(Text, pk=text_id, owner=request.user)
    study = services.start_study_session(request.user, activity, text=text, access=request.access_session)
    return JsonResponse({"id": study.pk, "beat_url": reverse("tracking:study_beat", args=[study.pk])})


@login_required
@require_POST
def study_beat(request, pk):
    study = get_object_or_404(StudySession, pk=pk, user=request.user)
    data = _payload(request)
    study = services.study_beat(study, active=bool(data.get("active")), end=bool(data.get("end")))
    return JsonResponse({"id": study.pk, "duration_seconds": study.duration_seconds, "ended": study.ended_at is not None})


@login_required
def session_history(request):
    services.expire_stale_sessions(user=request.user)
    qs = AccessSession.objects.filter(user=request.user).prefetch_related("study_sessions")
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    now = timezone.now()
    rows = [
        {
            "session": s,
            "elapsed": services.elapsed_seconds(s, now),
            "study": sum(st.duration_seconds for st in s.study_sessions.all()),
        }
        for s in page
    ]
    return render(request, "tracking/session_history.html", {"page": page, "rows": rows})
