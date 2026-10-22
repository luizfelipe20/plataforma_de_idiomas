from django.conf import settings
from django.utils.module_loading import import_string

from .base import AIProvider, AIServiceError, GeneratedQuestion, GeneratedQuiz, QuizRequest, SkippedKind

PROVIDERS = {
    "anthropic": "apps.core.ai.anthropic_provider.AnthropicProvider",
    "mock": "apps.core.ai.mock_provider.MockProvider",
}

_override = None


def get_provider():
    """Devolve o provedor configurado em `AI_PROVIDER` (ou o substituto de testes)."""
    if _override is not None:
        return _override
    path = PROVIDERS.get(settings.AI_PROVIDER, settings.AI_PROVIDER)
    return import_string(path)()


def set_provider_override(provider):
    """Permite injetar um provedor (usado nos testes)."""
    global _override
    _override = provider


__all__ = [
    "AIProvider",
    "AIServiceError",
    "GeneratedQuestion",
    "GeneratedQuiz",
    "QuizRequest",
    "SkippedKind",
    "get_provider",
    "set_provider_override",
]
