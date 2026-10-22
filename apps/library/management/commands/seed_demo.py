"""Cria um usuário de demonstração com textos, tags, quizzes, tentativas e sessões."""
import random
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.ai import set_provider_override
from apps.core.ai.mock_provider import MockProvider
from apps.library.models import Text
from apps.library.services.texts import get_or_create_tags, save_text
from apps.quizzes.models import QuizAttempt
from apps.quizzes.services import generate_quiz, start_attempt, submit_attempt
from apps.tracking.models import AccessSession, StudySession

TEXTS = [
    ("Remote work in tech", "en", ["Tecnologia", "Trabalho"],
     "Many software companies now allow employees to work from home. Developers say they save time because "
     "they no longer commute. However, managers worry that communication becomes harder. Some teams solve this "
     "with short daily video calls. Others prefer written updates in a shared document. Experts agree that clear "
     "goals matter more than the place where people work."),
    ("A weekend in Lisbon", "en", ["Viagens", "Iniciante"],
     "Last weekend Paul visited Lisbon for the first time. He took the famous yellow tram to the old castle. "
     "In the afternoon he tried a custard tart near the river. The weather was warm and sunny. On Sunday he "
     "walked through the narrow streets of Alfama and listened to fado music in a small restaurant."),
    ("La reunión de presupuesto", "es", ["Negócios", "Trabalho", "Avançado"],
     "La directora financiera presentó el presupuesto anual durante la reunión del lunes. Explicó que los costos "
     "de logística aumentaron un quince por ciento. Para compensar, la empresa reducirá los viajes internacionales. "
     "Algunos gerentes propusieron invertir en herramientas digitales. La decisión final se tomará en marzo."),
    ("Phrasal verbs at the office", "en", ["Vocabulário", "Gramática"],
     "Please fill out this form before the meeting. We need to figure out why the server went down. "
     "The manager will look into the problem tomorrow. Could you set up the projector? "
     "If you run out of paper, ask the assistant. Do not give up on the project."),
]


class Command(BaseCommand):
    help = "Cria o usuário 'demo' (senha 'demo12345') com dados de exemplo. Usa o provedor offline."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Apaga o usuário demo antes de recriar.")

    def handle(self, *args, reset=False, **options):
        if reset:
            User.objects.filter(username="demo").delete()
        user, created = User.objects.get_or_create(username="demo")
        if not created and Text.objects.filter(owner=user).exists():
            self.stdout.write("Usuário demo já possui dados. Use --reset para recriar.")
            return
        user.set_password("demo12345")
        user.save()

        set_provider_override(MockProvider())
        rng = random.Random(42)
        now = timezone.now()
        try:
            for index, (title, lang, tags, content) in enumerate(TEXTS):
                text = Text(owner=user, title=title, content=content, source_language=lang, target_language="pt")
                save_text(text, tags=get_or_create_tags(user, tags))
                skill = [0.85, 0.75, 0.35, 0.55][index]
                for _ in range(2):
                    quiz = generate_quiz(text, language="pt")
                    for _ in range(rng.randint(1, 3)):
                        attempt = start_attempt(quiz, user, rng.choice(["reading", "reading_audio", "audio_only"]))
                        selections = {}
                        for question in quiz.questions.prefetch_related("options"):
                            options = list(question.options.all())
                            right = rng.random() < skill
                            selections[question.pk] = next(o for o in options if o.is_correct == right).pk
                        submit_attempt(attempt, selections)
                        when = now - timedelta(days=rng.randint(0, 20), hours=rng.randint(0, 8))
                        QuizAttempt.objects.filter(pk=attempt.pk).update(started_at=when, completed_at=when + timedelta(minutes=4))
            for day in range(1, 15):
                start = now - timedelta(days=day, hours=rng.randint(1, 5))
                minutes = rng.randint(10, 70)
                end = start + timedelta(minutes=minutes)
                access = AccessSession.objects.create(
                    user=user, started_at=start, ended_at=end, last_seen_at=end, last_activity_at=end,
                    active_seconds=int(minutes * 60 * 0.8), duration_seconds=minutes * 60,
                    status=AccessSession.Status.CLOSED, end_reason=AccessSession.EndReason.LOGOUT,
                )
                StudySession.objects.create(
                    user=user, access_session=access, activity=rng.choice(StudySession.Activity.values),
                    started_at=start, last_beat_at=end, ended_at=end, duration_seconds=int(minutes * 60 * 0.6),
                )
        finally:
            set_provider_override(None)
        self.stdout.write(self.style.SUCCESS("Usuário 'demo' criado (senha: demo12345)."))
