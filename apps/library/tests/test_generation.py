from django.test import TestCase
from django.urls import reverse

from apps.core.ai import AIServiceError, set_provider_override
from apps.core.ai.mock_provider import MockProvider
from apps.core.tests.factories import FailingProvider, make_user
from apps.library.models import Tag, Text
from apps.library.services.generation import LEVELS, MAX_TEXTS_PER_REQUEST, TEXT_LENGTHS

PASSWORD = "senha-forte-123"


class CapturingProvider(MockProvider):
    def __init__(self):
        self.requests = []

    def generate_texts(self, request):
        self.requests.append(request)
        return super().generate_texts(request)


class FailingGenerationProvider(MockProvider):
    def generate_texts(self, request):
        raise AIServiceError("Serviço indisponível", retryable=True)


class FailingTranslationProvider(FailingProvider):
    """Gera os textos, mas falha ao traduzi-los."""

    def generate_texts(self, request):
        return MockProvider().generate_texts(request)


class TextGenerationViewTests(TestCase):
    def setUp(self):
        self.provider = CapturingProvider()
        set_provider_override(self.provider)
        self.addCleanup(set_provider_override, None)
        self.user = make_user()
        self.client.login(username=self.user.username, password=PASSWORD)
        self.url = reverse("library:text_generate")

    def post(self, follow=False, **overrides):
        data = {"prompt": "A trip to Lisbon", "source_language": "en", "target_language": "pt",
                "count": 1, "length": "short", "level": "", "new_tags": "", **overrides}
        return self.client.post(self.url, data, follow=follow)

    def test_page_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gerar textos com IA")

    def test_single_text_is_saved_translated_and_opened(self):
        response = self.post()
        text = Text.objects.get()
        self.assertRedirects(response, text.get_absolute_url())
        self.assertEqual((text.owner, text.source_language, text.target_language), (self.user, "en", "pt"))
        self.assertEqual(text.status, Text.Status.READY)
        self.assertTrue(text.chunks.exists())

    def test_several_texts_with_tags(self):
        tag = Tag.objects.create(owner=self.user, name="Viagens")
        response = self.post(follow=True, count=3, tags=[tag.pk], new_tags="Gerado por IA")
        self.assertRedirects(response, reverse("library:text_list"))
        self.assertContains(response, "3 textos criados")
        texts = Text.objects.all()
        self.assertEqual(texts.count(), 3)
        self.assertEqual(len({t.title for t in texts}), 3)
        for text in texts:
            self.assertEqual(sorted(t.name for t in text.tags.all()), ["Gerado por IA", "Viagens"])

    def test_request_sent_to_ai(self):
        self.post(count=2, length="long", level="beginner", prompt="  Um diálogo num hotel  ")
        request = self.provider.requests[0]
        self.assertEqual(request.prompt, "Um diálogo num hotel")
        self.assertEqual((request.source_language.code, request.target_language.code), ("en", "pt"))
        self.assertEqual(request.count, 2)
        self.assertEqual(request.words, TEXT_LENGTHS["long"][1])
        self.assertEqual(request.level, LEVELS["beginner"][1])

    def test_validation(self):
        for overrides in ({"prompt": "   "}, {"count": MAX_TEXTS_PER_REQUEST + 1}, {"count": 0},
                          {"target_language": "en"}):
            response = self.post(**overrides)
            self.assertEqual(response.status_code, 200, overrides)
            self.assertTrue(response.context["form"].errors, overrides)
        self.assertFalse(Text.objects.exists())
        self.assertEqual(self.provider.requests, [])

    def test_ai_failure_creates_nothing(self):
        set_provider_override(FailingGenerationProvider())
        response = self.post(count=2)
        self.assertContains(response, "Não foi possível gerar os textos")
        self.assertFalse(Text.objects.exists())

    def test_translation_failure_is_reported(self):
        set_provider_override(FailingTranslationProvider())
        response = self.post(follow=True, count=2)
        self.assertContains(response, "A tradução falhou em 2 deles")
        self.assertEqual(Text.objects.filter(status=Text.Status.ERROR).count(), 2)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
