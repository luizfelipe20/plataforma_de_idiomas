from django.core.management.base import BaseCommand

from apps.quizzes.services import mark_abandoned_attempts
from apps.tracking.services import close_stale_study_sessions, expire_stale_sessions


class Command(BaseCommand):
    help = (
        "Encerra sessões de acesso abandonadas ou inativas, sessões de estudo sem "
        "heartbeat e tentativas de quiz abandonadas. Agende via cron (ex.: a cada 5 min)."
    )

    def handle(self, *args, **options):
        sessions = expire_stale_sessions()
        studies = close_stale_study_sessions()
        attempts = mark_abandoned_attempts()
        self.stdout.write(
            self.style.SUCCESS(
                f"Sessões encerradas: {sessions} · estudos encerrados: {studies} · tentativas abandonadas: {attempts}"
            )
        )
