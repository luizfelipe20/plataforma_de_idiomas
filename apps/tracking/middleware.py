from django.contrib import messages
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from . import services
from .models import AccessSession

PASSIVE_VIEW_NAMES = {"tracking:heartbeat", "tracking:study_beat"}


class AccessSessionMiddleware:
    """
    Mantém a sessão de acesso do usuário autenticado.

    - Associa `request.access_session` à sessão ativa (criando uma se necessário).
    - Encerra a sessão quando a regra de inatividade/abandono é violada, mesmo
      que o JavaScript não esteja rodando (a regra é aplicada no servidor).
    - Cada requisição de página conta como atividade; os heartbeats informam
      a atividade explicitamente e por isso não são tocados aqui.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _is_passive(request):
        try:
            return resolve(request.path_info).view_name in PASSIVE_VIEW_NAMES
        except Resolver404:
            return False

    def __call__(self, request):
        request.access_session = None
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or request.path_info.startswith(("/static/", "/admin/")):
            return self.get_response(request)

        now = timezone.now()
        access = None
        access_id = request.session.get(services.SESSION_KEY)
        if access_id:
            access = AccessSession.objects.filter(pk=access_id, user=user).first()

        if access is not None and (not access.is_active or services.expire_if_needed(access, now)):
            return self._expired_response(request, access)

        if access is None:
            # Ex.: sessão criada antes do recurso ou dados de sessão perdidos.
            services.expire_stale_sessions(now, user=user)
            access = services.start_access_session(user, request.session.session_key or "", now)
            request.session[services.SESSION_KEY] = access.pk
        elif not self._is_passive(request):
            access = services.touch(access, now, activity=True)

        request.access_session = access
        return self.get_response(request)

    def _expired_response(self, request, access):
        reason = access.get_end_reason_display().lower() if access.end_reason else "expiração"
        logout(request)
        if request.headers.get("x-requested-with") == "fetch" or request.content_type == "application/json":
            return JsonResponse({"status": "expired", "reason": access.end_reason, "login_url": reverse("accounts:login")}, status=401)
        messages.info(request, f"Sua sessão foi encerrada por {reason}. Entre novamente para continuar.")
        return redirect(f"{reverse('accounts:login')}?next={request.get_full_path()}")
