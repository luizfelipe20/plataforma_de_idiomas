from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.utils import timezone

from . import services
from .models import AccessSession


@receiver(user_logged_in)
def start_session_on_login(sender, request, user, **kwargs):
    now = timezone.now()
    services.expire_stale_sessions(now, user=user)
    previous_id = request.session.get(services.SESSION_KEY)
    if previous_id:
        previous = AccessSession.objects.filter(pk=previous_id).first()
        if previous:
            services.close_access_session(previous, now, AccessSession.EndReason.REPLACED)
    access = services.start_access_session(user, request.session.session_key or "", now)
    request.session[services.SESSION_KEY] = access.pk
    request.access_session = access


@receiver(user_logged_out)
def close_session_on_logout(sender, request, user, **kwargs):
    if request is None or user is None:
        return
    access_id = request.session.get(services.SESSION_KEY)
    access = AccessSession.objects.filter(pk=access_id, user=user).first() if access_id else None
    if access is not None:
        services.close_access_session(access, timezone.now(), AccessSession.EndReason.LOGOUT)
