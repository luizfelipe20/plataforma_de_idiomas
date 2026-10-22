"""Utilitários compartilhados pelos testes."""
from django.contrib.auth.models import User

from apps.core.ai import AIServiceError, GeneratedQuestion, GeneratedQuiz
from apps.core.ai.mock_provider import MockProvider
from apps.library.models import Text
from apps.library.services.texts import save_text

SAMPLE = (
    "Maria works as a software engineer in Lisbon. Every morning she takes the tram to the office. "
    "Her team builds tools that help hospitals schedule appointments. Last year they launched a mobile app. "
    "The app reduced waiting times by thirty percent. Now Maria is learning Japanese because her company "
    "plans to open an office in Tokyo. She studies for twenty minutes every evening. "
    "She is happy because her goals are clear and the classes are small."
)


def make_user(username="ana", password="senha-forte-123"):
    return User.objects.create_user(username=username, password=password)


def make_text(owner, *, title="Maria em Lisboa", content=SAMPLE, source="en", target="pt", tags=(), translate=True):
    text = Text(owner=owner, title=title, content=content, source_language=source, target_language=target)
    return save_text(text, tags=tags, translate=translate)


class FailingProvider(MockProvider):
    """Simula indisponibilidade do serviço externo."""

    def translate_segments(self, *args, **kwargs):
        raise AIServiceError("Serviço indisponível", retryable=True)

    def generate_quiz(self, request):
        raise AIServiceError("Serviço indisponível", retryable=True)


class RecordingProvider(MockProvider):
    """Registra as chamadas para verificar o que é enviado à IA."""

    def __init__(self):
        self.translate_calls = []
        self.quiz_calls = []

    def translate_segments(self, segments, source_language, target_language):
        self.translate_calls.append(list(segments))
        return super().translate_segments(segments, source_language, target_language)

    def generate_quiz(self, request):
        self.quiz_calls.append(request)
        return super().generate_quiz(request)


class ScriptedProvider(MockProvider):
    """Devolve respostas pré-definidas, uma por chamada, e guarda os pedidos recebidos."""

    def __init__(self, quizzes):
        self.quizzes = list(quizzes)
        self.requests = []

    @property
    def calls(self):
        return len(self.requests)

    def generate_quiz(self, request):
        self.requests.append(request)
        return self.quizzes.pop(0)


OPTIONS_5 = ["alpha", "bravo", "charlie", "delta", "echo"]


def question(prompt, *, kind="comprehension", options=None, correct=0, passage=""):
    return GeneratedQuestion(prompt=prompt, options=list(options or OPTIONS_5), correct_index=correct,
                             explanation="porque sim", kind=kind, passage=passage)


def quiz_of(prompts, *, options=None, correct=0, kind="comprehension", skipped=()):
    return GeneratedQuiz(
        questions=[question(p, kind=kind, options=options, correct=correct) for p in prompts],
        skipped=list(skipped),
        model="scripted",
    )
