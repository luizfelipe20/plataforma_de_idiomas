"""
Contrato comum dos provedores de IA.

As regras de negócio (tradução e geração de quizzes) dependem apenas desta
interface; trocar de provedor é uma questão de configuração (`AI_PROVIDER`).
"""
from dataclasses import dataclass, field


class AIServiceError(Exception):
    """Falha ao se comunicar com o serviço de IA ou resposta inválida."""

    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class GeneratedQuestion:
    prompt: str
    options: list
    correct_index: int
    explanation: str = ""
    kind: str = ""
    passage: str = ""


@dataclass
class SkippedKind:
    """Tipo que a IA não conseguiu gerar sem inventar informações (texto insuficiente)."""

    kind: str
    reason: str


@dataclass
class GeneratedQuiz:
    questions: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    model: str = ""


@dataclass(frozen=True)
class QuizRequest:
    """
    Pedido de geração de perguntas.

    `plan` é uma lista de dicionários {"kind", "count", "label", "instructions"},
    montada pela aplicação a partir do registro de tipos de pergunta.
    """

    text: str
    source_language: object
    quiz_language: object
    explanation_language: object
    plan: tuple
    num_options: int
    avoid_questions: tuple = ()
    variation_seed: int = 0


class AIProvider:
    name = "base"

    def translate_segments(self, segments, source_language, target_language):
        """Traduz uma lista de trechos e devolve uma lista de mesmo tamanho."""
        raise NotImplementedError

    def generate_quiz(self, request):
        """Gera as perguntas descritas em `request` (QuizRequest) e devolve um GeneratedQuiz."""
        raise NotImplementedError
