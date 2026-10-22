"""
Regras de sessão de acesso e de tempo de estudo.

Princípios:
- Os horários de referência são sempre os do servidor; o navegador apenas
  exibe o contador e envia sinais (heartbeats).
- Uma sessão encerrada é imutável: o encerramento usa uma atualização
  condicional (status=ativa), portanto nunca é contabilizado duas vezes.
- O tempo conectado diário é a união dos intervalos das sessões, de modo que
  sessões simultâneas (duas abas/dispositivos) não somam tempo em dobro.
- O tempo de estudo é creditado por heartbeat e limitado ao intervalo desde o
  último crédito de qualquer sessão de estudo do usuário, evitando sobreposição.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from .models import AccessSession, StudySession

SESSION_KEY = "access_session_id"


# ---------------------------------------------------------------------------
# Sessões de acesso
# ---------------------------------------------------------------------------
def start_access_session(user, session_key="", now=None):
    now = now or timezone.now()
    if session_key:
        for previous in AccessSession.objects.filter(session_key=session_key, status=AccessSession.Status.ACTIVE):
            close_access_session(previous, now, AccessSession.EndReason.REPLACED)
    return AccessSession.objects.create(
        user=user,
        session_key=session_key or "",
        started_at=now,
        last_seen_at=now,
        last_activity_at=now,
    )


def close_access_session(access, ended_at, reason):
    """Encerra a sessão uma única vez. Retorna False se ela já estava encerrada."""
    ended_at = max(ended_at, access.started_at)
    duration = int((ended_at - access.started_at).total_seconds())
    updated = AccessSession.objects.filter(pk=access.pk, status=AccessSession.Status.ACTIVE).update(
        status=AccessSession.Status.CLOSED,
        ended_at=ended_at,
        duration_seconds=duration,
        end_reason=reason,
    )
    if updated:
        access.status = AccessSession.Status.CLOSED
        access.ended_at = ended_at
        access.duration_seconds = duration
        access.end_reason = reason
        StudySession.objects.filter(access_session=access, ended_at__isnull=True).update(ended_at=ended_at)
    return bool(updated)


def expiry_for(access, now=None):
    """(momento_de_término, motivo) se a sessão deve expirar agora, senão None."""
    if not access.is_active:
        return None
    now = now or timezone.now()
    abandon = timedelta(seconds=settings.SESSION_ABANDON_TIMEOUT_SECONDS)
    inactivity = timedelta(seconds=settings.SESSION_INACTIVITY_TIMEOUT_SECONDS)
    if now - access.last_seen_at > abandon:
        return access.last_seen_at, AccessSession.EndReason.ABANDONED
    if now - access.last_activity_at > inactivity:
        return min(now, access.last_activity_at + inactivity), AccessSession.EndReason.INACTIVITY
    return None


def expire_if_needed(access, now=None):
    expiry = expiry_for(access, now)
    if expiry:
        close_access_session(access, *expiry)
        return True
    return False


def expire_stale_sessions(now=None, user=None):
    """Encerra sessões abandonadas/inativas. Usado por middleware e comando de gestão."""
    now = now or timezone.now()
    qs = AccessSession.objects.filter(status=AccessSession.Status.ACTIVE)
    if user is not None:
        qs = qs.filter(user=user)
    closed = sum(expire_if_needed(access, now) for access in qs)
    close_stale_study_sessions(now, user=user)
    return closed


@transaction.atomic
def touch(access, now=None, activity=True):
    """Registra um sinal do navegador e, se `activity`, uma atividade do usuário."""
    now = now or timezone.now()
    access = AccessSession.objects.select_for_update().get(pk=access.pk)
    if not access.is_active:
        return access
    fields = ["last_seen_at"]
    access.last_seen_at = max(now, access.last_seen_at)
    if activity:
        gap = (now - access.last_activity_at).total_seconds()
        if 0 < gap <= settings.SESSION_ACTIVE_GAP_SECONDS:
            access.active_seconds += int(gap)
            fields.append("active_seconds")
        access.last_activity_at = max(now, access.last_activity_at)
        fields.append("last_activity_at")
    access.save(update_fields=fields)
    return access


def elapsed_seconds(access, now=None):
    if access.duration_seconds is not None:
        return access.duration_seconds
    now = now or timezone.now()
    return max(int((now - access.started_at).total_seconds()), 0)


# ---------------------------------------------------------------------------
# Sessões de estudo
# ---------------------------------------------------------------------------
def start_study_session(user, activity, *, text=None, access=None, now=None):
    now = now or timezone.now()
    return StudySession.objects.create(
        user=user, access_session=access, text=text, activity=activity, started_at=now, last_beat_at=now
    )


@transaction.atomic
def study_beat(study, now=None, active=True, end=False):
    """
    Credita o tempo de estudo desde o último heartbeat.

    O crédito começa no heartbeat anterior desta sessão ou no crédito mais recente
    de *qualquer* outra sessão de estudo do usuário (o que for mais tarde), de modo
    que duas abas estudando ao mesmo tempo não contam em dobro. Heartbeats
    inativos (aba oculta, sem interação) não creditam tempo.
    """
    now = now or timezone.now()
    study = StudySession.objects.select_for_update().get(pk=study.pk)
    if study.ended_at is not None:
        return study
    if active:
        others_last = (
            StudySession.objects.filter(user_id=study.user_id, last_credited_at__lte=now)
            .exclude(pk=study.pk)
            .aggregate(m=Max("last_credited_at"))["m"]
        )
        credit_from = max(filter(None, [study.last_beat_at, others_last]))
        delta = int((now - credit_from).total_seconds())
        if delta > 0:
            study.duration_seconds += min(delta, settings.STUDY_MAX_CREDIT_SECONDS)
        study.last_credited_at = now
    study.last_beat_at = max(now, study.last_beat_at)
    if end:
        study.ended_at = study.last_beat_at
    study.save()
    return study


def close_stale_study_sessions(now=None, user=None):
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.SESSION_ABANDON_TIMEOUT_SECONDS)
    qs = StudySession.objects.filter(ended_at__isnull=True, last_beat_at__lt=cutoff)
    if user is not None:
        qs = qs.filter(user=user)
    count = 0
    for study in qs:
        count += StudySession.objects.filter(pk=study.pk, ended_at__isnull=True).update(ended_at=study.last_beat_at)
    return count


# ---------------------------------------------------------------------------
# Estatísticas de tempo
# ---------------------------------------------------------------------------
def _day_bounds(day):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    return start, start + timedelta(days=1)


def _merged_seconds(intervals):
    """Soma a união de intervalos (sobreposições contam uma vez só)."""
    total, current_start, current_end = 0, None, None
    for start, end in sorted(intervals):
        if current_end is None or start > current_end:
            if current_end is not None:
                total += (current_end - current_start).total_seconds()
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        total += (current_end - current_start).total_seconds()
    return int(total)


def connected_seconds_between(user, start, end, now=None):
    now = now or timezone.now()
    intervals = []
    qs = AccessSession.objects.filter(user=user, started_at__lt=end).exclude(ended_at__lt=start)
    for access in qs:
        s_end = access.ended_at or min(now, access.last_seen_at + timedelta(seconds=settings.SESSION_HEARTBEAT_SECONDS))
        s_end = max(s_end, access.started_at)
        lo, hi = max(access.started_at, start), min(s_end, end)
        if hi > lo:
            intervals.append((lo, hi))
    return _merged_seconds(intervals)


def connected_seconds_on(user, day, now=None):
    return connected_seconds_between(user, *_day_bounds(day), now=now)


def study_seconds_between(user, start, end):
    return (
        StudySession.objects.filter(user=user, started_at__gte=start, started_at__lt=end).aggregate(
            s=Sum("duration_seconds")
        )["s"]
        or 0
    )


def time_summary(user, now=None, current_access=None, average_days=30):
    now = now or timezone.now()
    today = timezone.localdate(now)
    day_start, day_end = _day_bounds(today)
    week_start = _day_bounds(today - timedelta(days=today.weekday()))[0]
    month_start = _day_bounds(today.replace(day=1))[0]
    avg_start = _day_bounds(today - timedelta(days=average_days - 1))[0]
    avg_total = study_seconds_between(user, avg_start, day_end)
    return {
        "current_session_seconds": elapsed_seconds(current_access, now) if current_access else 0,
        "current_active_seconds": current_access.active_seconds if current_access else 0,
        "connected_today": connected_seconds_between(user, day_start, day_end, now),
        "study_today": study_seconds_between(user, day_start, day_end),
        "study_week": study_seconds_between(user, week_start, day_end),
        "study_month": study_seconds_between(user, month_start, day_end),
        "study_daily_average": avg_total // average_days,
        "average_days": average_days,
    }


def daily_study_series(user, days=14, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    labels, study, connected = [], [], []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        start, end = _day_bounds(day)
        labels.append(day.strftime("%d/%m"))
        study.append(round(study_seconds_between(user, start, end) / 60, 1))
        connected.append(round(connected_seconds_between(user, start, end, now) / 60, 1))
    return {"labels": labels, "study_minutes": study, "connected_minutes": connected}


def weekly_study_series(user, weeks=8, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    monday = today - timedelta(days=today.weekday())
    labels, values = [], []
    for offset in range(weeks - 1, -1, -1):
        week_day = monday - timedelta(weeks=offset)
        start = _day_bounds(week_day)[0]
        end = start + timedelta(days=7)
        labels.append(week_day.strftime("%d/%m"))
        values.append(round(study_seconds_between(user, start, end) / 60, 1))
    return {"labels": labels, "study_minutes": values}
