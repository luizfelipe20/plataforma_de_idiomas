import json

from django.test import TestCase
from django.urls import reverse

from apps.core.ai import AIServiceError, GeneratedPracticeText, set_provider_override
from apps.core.ai.mock_provider import MockProvider
from apps.core.tests.factories import make_text, make_user
from apps.library.models import Text
from apps.vocabulary import services
from apps.vocabulary.models import DifficultTerm

PASSWORD = "senha-forte-123"


class FailingPracticeProvider(MockProvider):
    def explain_terms(self, *args, **kwargs):
        raise AIServiceError("Serviço indisponível", retryable=True)

    def generate_practice_text(self, request):
        raise AIServiceError("Serviço indisponível", retryable=True)


class IncompletePracticeProvider(MockProvider):
    """Ignora o termo “waiting times”, como uma IA que não seguiu a instrução."""

    def generate_practice_text(self, request):
        used = " ".join(f"I like {term}." for term, _ in request.terms if term != "waiting times")
        return GeneratedPracticeText(title="Parcial", content=used, model="mock")


class MarkTermTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.text = make_text(self.user)

    def test_mark_creates_term_with_text_language_and_context(self):
        term, created = services.mark_term(self.user, "  software\n engineer. ", self.text, "Maria works as a software engineer.")
        self.assertTrue(created)
        self.assertEqual(term.term, "software engineer")
        self.assertEqual(term.language, "en")
        self.assertEqual(term.source_text, self.text)
        self.assertEqual(term.times_marked, 1)

    def test_marking_again_ignores_case_and_counts(self):
        services.mark_term(self.user, "tram", self.text)
        term, created = services.mark_term(self.user, "Tram", self.text, "She takes the tram.")
        self.assertFalse(created)
        self.assertEqual(term.times_marked, 2)
        self.assertEqual(term.context, "She takes the tram.")
        self.assertEqual(DifficultTerm.objects.count(), 1)

    def test_same_term_in_another_language_is_separate(self):
        services.mark_term(self.user, "real", self.text)
        spanish = make_text(self.user, title="ES", content="Es un caso real.", source="es")
        services.mark_term(self.user, "real", spanish)
        self.assertEqual(DifficultTerm.objects.count(), 2)

    def test_empty_selection_is_rejected(self):
        with self.assertRaises(ValueError):
            services.mark_term(self.user, " ... ", self.text)


class TermViewsTests(TestCase):
    def setUp(self):
        set_provider_override(MockProvider())
        self.addCleanup(set_provider_override, None)
        self.user = make_user()
        self.client.login(username=self.user.username, password=PASSWORD)
        self.text = make_text(self.user)

    def mark(self, term, text=None, **extra):
        payload = {"text_id": (text or self.text).pk, "term": term, **extra}
        return self.client.post(reverse("vocabulary:term_mark"), json.dumps(payload), content_type="application/json")

    def test_mark_via_fetch(self):
        response = self.mark("tram", context="Every morning she takes the tram to the office.")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["term"], "tram")
        response = self.mark("TRAM")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["times_marked"], 2)

    def test_cannot_mark_in_other_users_text(self):
        other_text = make_text(make_user(username="outro"))
        self.assertEqual(self.mark("tram", text=other_text).status_code, 404)
        self.assertFalse(DifficultTerm.objects.exists())

    def test_invalid_mark_requests(self):
        self.assertEqual(self.mark("   ").status_code, 400)
        response = self.client.post(reverse("vocabulary:term_mark"), "não é json", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_text_pages_receive_terms_of_the_text_language(self):
        self.mark("tram")
        services.mark_term(self.user, "real", make_text(self.user, title="ES", content="Es real.", source="es"))
        for url in [self.text.get_absolute_url(), reverse("library:study", args=[self.text.pk])]:
            response = self.client.get(url)
            self.assertEqual([t["term"] for t in response.context["difficult_terms"]], ["tram"])
            self.assertContains(response, 'id="difficult-terms"')
            self.assertContains(response, f'data-term-source="{self.text.pk}"')

    def test_marked_terms_link_to_their_detail_page(self):
        data = self.mark("tram").json()
        term = DifficultTerm.objects.get(pk=data["id"])
        self.assertEqual(data["url"], reverse("vocabulary:term_detail", args=[term.pk]))
        response = self.client.get(self.text.get_absolute_url())
        self.assertEqual(response.context["difficult_terms"][0]["url"], data["url"])
        self.assertContains(self.client.get(reverse("vocabulary:term_list")), f'href="{data["url"]}"')

    def test_detail_page_shows_term_and_texts_containing_it(self):
        term = DifficultTerm.objects.get(pk=self.mark("tram", context="She takes the tram.").json()["id"])
        make_text(self.user, title="Sem o termo", content="Nothing related here.")
        response = self.client.get(term.get_absolute_url())
        self.assertContains(response, "She takes the tram.")
        self.assertEqual([t.title for t in response.context["texts_with_term"]], [self.text.title])
        self.assertContains(response, f'name="terms" value="{term.pk}"')

    def test_detail_is_private(self):
        term = DifficultTerm.objects.get(pk=self.mark("tram").json()["id"])
        self.client.logout()
        other = make_user(username="outro")
        self.client.login(username=other.username, password=PASSWORD)
        self.assertEqual(self.client.get(term.get_absolute_url()).status_code, 404)

    def test_actions_from_detail_return_to_it(self):
        term = DifficultTerm.objects.get(pk=self.mark("tram").json()["id"])
        url = term.get_absolute_url()
        response = self.client.post(reverse("vocabulary:term_update", args=[term.pk]), {"meaning": "bonde", "next": url})
        self.assertRedirects(response, url)
        response = self.client.post(reverse("vocabulary:term_explain"), {"terms": [term.pk], "next": url})
        self.assertRedirects(response, url)

    def test_list_filters_and_orders_by_times_marked(self):
        self.mark("tram")
        self.mark("hospitals")
        self.mark("hospitals")
        response = self.client.get(reverse("vocabulary:term_list"))
        self.assertEqual([t.term for t in response.context["page"]], ["hospitals", "tram"])
        response = self.client.get(reverse("vocabulary:term_list"), {"q": "tra"})
        self.assertEqual([t.term for t in response.context["page"]], ["tram"])
        response = self.client.get(reverse("vocabulary:term_list"), {"idioma": "es"})
        self.assertEqual(list(response.context["page"]), [])

    def test_update_meaning_and_delete(self):
        term = DifficultTerm.objects.get(pk=self.mark("tram").json()["id"])
        self.client.post(reverse("vocabulary:term_update", args=[term.pk]), {"meaning": "bonde"})
        term.refresh_from_db()
        self.assertEqual(term.meaning, "bonde")
        response = self.client.post(reverse("vocabulary:term_delete", args=[term.pk]))
        self.assertRedirects(response, reverse("vocabulary:term_list"))
        self.assertFalse(DifficultTerm.objects.exists())

    def test_other_users_cannot_change_terms(self):
        term = DifficultTerm.objects.get(pk=self.mark("tram").json()["id"])
        self.client.logout()
        other = make_user(username="outro")
        self.client.login(username=other.username, password=PASSWORD)
        self.assertEqual(self.client.post(reverse("vocabulary:term_delete", args=[term.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("vocabulary:term_update", args=[term.pk]), {"meaning": "x"}).status_code, 404)

    def test_redirect_ignores_external_next(self):
        term_id = self.mark("tram").json()["id"]
        response = self.client.post(reverse("vocabulary:term_delete", args=[term_id]), {"next": "https://evil.example/"})
        self.assertRedirects(response, reverse("vocabulary:term_list"))

    def test_explain_fills_meaning_in_translation_language(self):
        term_id = self.mark("tram").json()["id"]
        self.client.post(reverse("vocabulary:term_explain"), {"terms": [term_id]})
        self.assertIn("[PT · explicação simulada] tram", DifficultTerm.objects.get(pk=term_id).meaning)

    def test_explain_failure_keeps_terms(self):
        set_provider_override(FailingPracticeProvider())
        term_id = self.mark("tram").json()["id"]
        response = self.client.post(reverse("vocabulary:term_explain"), {"terms": [term_id]}, follow=True)
        self.assertContains(response, "Não foi possível explicar")
        self.assertEqual(DifficultTerm.objects.get(pk=term_id).meaning, "")


class PracticeTextTests(TestCase):
    def setUp(self):
        set_provider_override(MockProvider())
        self.addCleanup(set_provider_override, None)
        self.user = make_user()
        self.client.login(username=self.user.username, password=PASSWORD)
        self.text = make_text(self.user)
        self.terms = [services.mark_term(self.user, t, self.text)[0] for t in ("tram", "appointments", "waiting times")]

    def post(self, terms, follow=False, **extra):
        data = {"terms": [t.pk for t in terms], "length": "short", **extra}
        return self.client.post(reverse("vocabulary:practice_create"), data, follow=follow)

    def test_creates_text_with_all_terms_linked_and_tagged(self):
        response = self.post(self.terms, topic="viagem")
        practice = Text.objects.exclude(pk=self.text.pk).get()
        self.assertRedirects(response, practice.get_absolute_url())
        for term in self.terms:
            self.assertIn(term.term, practice.content)
        self.assertEqual(practice.source_language, "en")
        self.assertEqual(practice.target_language, "pt")
        self.assertEqual(practice.status, Text.Status.READY)
        self.assertTrue(practice.chunks.exists())
        self.assertEqual(set(practice.practiced_terms.all()), set(self.terms))
        self.assertEqual([t.name for t in practice.tags.all()], [services.PRACTICE_TAG])
        detail = self.client.get(practice.get_absolute_url())
        self.assertContains(detail, "Texto de prática dos termos")

    def test_request_sent_to_ai(self):
        captured = []

        class Capturing(MockProvider):
            def generate_practice_text(self, request):
                captured.append(request)
                return super().generate_practice_text(request)

        set_provider_override(Capturing())
        self.terms[0].meaning = "bonde"
        self.terms[0].save()
        self.post(self.terms[:2], topic="  uma   viagem ")
        request = captured[0]
        self.assertEqual(request.terms, (("tram", "bonde"), ("appointments", "")))
        self.assertEqual(request.source_language.code, "en")
        self.assertEqual(request.words, services.PRACTICE_LENGTHS["short"][1])
        self.assertEqual(request.topic, "uma viagem")

    def test_warns_about_terms_the_ai_left_out(self):
        set_provider_override(IncompletePracticeProvider())
        response = self.post(self.terms, follow=True)
        self.assertContains(response, "a IA não usou: “waiting times”")

    def test_requires_terms_of_a_single_language(self):
        spanish = make_text(self.user, title="ES", content="Es un caso real.", source="es")
        mixed = [self.terms[0], services.mark_term(self.user, "real", spanish)[0]]
        response = self.post(mixed, follow=True)
        self.assertContains(response, "um único idioma")
        self.assertEqual(Text.objects.count(), 2)

    def test_requires_selection_and_respects_limit(self):
        self.assertContains(self.post([], follow=True), "Selecione ao menos um termo")
        many = [services.mark_term(self.user, f"word{i}", self.text)[0] for i in range(services.MAX_PRACTICE_TERMS + 1)]
        self.assertContains(self.post(many, follow=True), "no máximo")

    def test_cannot_use_other_users_terms(self):
        other = make_user(username="outro")
        foreign = services.mark_term(other, "tram", make_text(other))[0]
        self.assertContains(self.post([foreign], follow=True), "Termo inválido")

    def test_ai_failure_creates_nothing(self):
        set_provider_override(FailingPracticeProvider())
        response = self.post(self.terms, follow=True)
        self.assertContains(response, "Não foi possível gerar o texto de prática")
        self.assertEqual(Text.objects.count(), 1)
