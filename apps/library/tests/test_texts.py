from django.test import TestCase
from django.urls import reverse

from apps.core.ai import set_provider_override
from apps.core.tests.factories import FailingProvider, RecordingProvider, make_text, make_user
from apps.library.models import Tag, Text
from apps.library.services.texts import parse_tag_names, save_text, translate_text

LONG = " ".join(f"This is sentence {i} of a long story about travel and work." for i in range(40))


class ProviderOverrideMixin:
    provider = None

    def use_provider(self, provider):
        set_provider_override(provider)
        self.addCleanup(set_provider_override, None)
        return provider


class TextServiceTests(ProviderOverrideMixin, TestCase):
    def setUp(self):
        self.user = make_user()

    def test_save_creates_ordered_chunks_and_translation(self):
        text = make_text(self.user, content=LONG)
        chunks = list(text.chunks.all())
        self.assertGreater(len(chunks), 1)
        self.assertEqual([c.order for c in chunks], list(range(len(chunks))))
        self.assertEqual("".join(c.content for c in chunks), LONG)
        self.assertEqual(text.status, Text.Status.READY)
        self.assertTrue(all(c.translation and c.translation_language == "pt" for c in chunks))
        self.assertTrue(text.translation)

    def test_default_translation_language_is_portuguese(self):
        text = Text(owner=self.user, title="t", content="Hello", source_language="en")
        self.assertEqual(text.target_language, "pt")

    def test_translation_failure_is_recorded_not_raised(self):
        self.use_provider(FailingProvider())
        text = make_text(self.user)
        self.assertEqual(text.status, Text.Status.ERROR)
        self.assertIn("indisponível", text.status_message)
        # Nova tentativa após o serviço voltar
        set_provider_override(None)
        self.assertTrue(translate_text(text))
        text.refresh_from_db()
        self.assertEqual(text.status, Text.Status.READY)

    def test_translations_are_reused_for_unchanged_chunks(self):
        provider = self.use_provider(RecordingProvider())
        text = make_text(self.user, content=LONG)
        self.assertEqual(len(provider.translate_calls), 1)
        text.content = LONG + " A brand new final sentence."
        save_text(text)
        # Somente os trechos alterados são enviados novamente à IA
        self.assertEqual(len(provider.translate_calls), 2)
        self.assertLess(len(provider.translate_calls[-1]), text.chunks.count())
        self.assertTrue(all(c.translation for c in text.chunks.all()))

    def test_changing_target_language_triggers_retranslation(self):
        provider = self.use_provider(RecordingProvider())
        text = make_text(self.user)
        calls = len(provider.translate_calls)
        text.target_language = "es"
        save_text(text)
        self.assertEqual(len(provider.translate_calls), calls + 1)
        self.assertTrue(all(c.translation_language == "es" for c in text.chunks.all()))

    def test_unchanged_text_is_not_retranslated(self):
        provider = self.use_provider(RecordingProvider())
        text = make_text(self.user)
        calls = len(provider.translate_calls)
        text.title = "Novo título"
        save_text(text)
        self.assertEqual(len(provider.translate_calls), calls)


class TagTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_parse_tag_names(self):
        self.assertEqual(parse_tag_names(" Viagens, trabalho ,, viagens , Alto  Nível "), ["Viagens", "trabalho", "Alto Nível"])

    def test_new_tags_created_inline_and_reused(self):
        first = make_text(self.user)
        save_text(first, new_tag_names="Tecnologia, Trabalho")
        second = make_text(self.user, title="Outro")
        save_text(second, new_tag_names="tecnologia")
        self.assertEqual(Tag.objects.filter(owner=self.user).count(), 2)
        tech = Tag.objects.get(name="Tecnologia")
        self.assertEqual(set(tech.texts.all()), {first, second})

    def test_deleting_tag_keeps_texts(self):
        tag = Tag.objects.create(owner=self.user, name="Viagens")
        text = make_text(self.user, tags=[tag])
        tag.delete()
        self.assertTrue(Text.objects.filter(pk=text.pk).exists())
        self.assertEqual(text.tags.count(), 0)

    def test_tag_names_are_unique_per_user_case_insensitive(self):
        self.client.force_login(self.user)
        self.client.post(reverse("library:tag_list"), {"name": "Gramática"})
        response = self.client.post(reverse("library:tag_list"), {"name": "gramática"})
        self.assertContains(response, "Já existe")
        other = make_user("beto")
        Tag.objects.create(owner=other, name="Gramática")  # outro usuário pode usar o mesmo nome
        self.assertEqual(Tag.objects.filter(name__iexact="gramática").count(), 2)

    def test_rename_and_delete_views(self):
        self.client.force_login(self.user)
        tag = Tag.objects.create(owner=self.user, name="Negocios")
        text = make_text(self.user, tags=[tag])
        self.client.post(reverse("library:tag_update", args=[tag.pk]), {"name": "Negócios"})
        tag.refresh_from_db()
        self.assertEqual(tag.name, "Negócios")
        self.client.post(reverse("library:tag_delete", args=[tag.pk]))
        self.assertFalse(Tag.objects.filter(pk=tag.pk).exists())
        self.assertTrue(Text.objects.filter(pk=text.pk).exists())


class LibraryViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.tech = Tag.objects.create(owner=self.user, name="Tecnologia")
        self.work = Tag.objects.create(owner=self.user, name="Trabalho")
        self.a = make_text(self.user, title="Robots", content="Robots build cars.", tags=[self.tech])
        self.b = make_text(self.user, title="Office", content="Meetings at the office.", tags=[self.tech, self.work])
        self.c = make_text(self.user, title="Viaje", content="Viajar es bueno.", source="es", tags=[self.work])

    def titles(self, **params):
        response = self.client.get(reverse("library:text_list"), params)
        return {t.title for t in response.context["page"]}

    def test_detail_page_offers_focus_mode(self):
        page = self.client.get(self.a.get_absolute_url())
        self.assertContains(page, "data-focus-toggle", count=2)  # ligar (cabeçalho) e sair (barra)
        self.assertContains(page, '<span class="focus-title">Robots</span>', html=True)
        self.assertContains(page, 'id="quizzes" data-focus-hide')

    def test_search_title_and_content(self):
        self.assertEqual(self.titles(q="robots"), {"Robots"})
        self.assertEqual(self.titles(q="meetings"), {"Office"})

    def test_filter_tags_any_and_all(self):
        self.assertEqual(self.titles(tag=[self.tech.pk, self.work.pk]), {"Robots", "Office", "Viaje"})
        self.assertEqual(self.titles(tag=[self.tech.pk, self.work.pk], match="all"), {"Office"})

    def test_combined_filters(self):
        self.assertEqual(self.titles(tag=self.work.pk, lang="es"), {"Viaje"})

    def test_sort_by_title(self):
        response = self.client.get(reverse("library:text_list"), {"sort": "title"})
        self.assertEqual([t.title for t in response.context["page"]], ["Office", "Robots", "Viaje"])

    def test_tag_counts(self):
        response = self.client.get(reverse("library:text_list"))
        counts = {t.name: t.text_count for t in response.context["tags"]}
        self.assertEqual(counts, {"Tecnologia": 2, "Trabalho": 2})

    def test_create_text_with_new_tags(self):
        response = self.client.post(
            reverse("library:text_create"),
            {"title": "Nova", "content": "Some content here.", "source_language": "en",
             "target_language": "pt", "quiz_language": "", "tags": [self.tech.pk], "new_tags": "Iniciante"},
        )
        text = Text.objects.get(title="Nova")
        self.assertRedirects(response, text.get_absolute_url())
        self.assertEqual({t.name for t in text.tags.all()}, {"Tecnologia", "Iniciante"})

    def test_same_source_and_target_language_rejected(self):
        response = self.client.post(
            reverse("library:text_create"),
            {"title": "X", "content": "abc", "source_language": "en", "target_language": "en"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Text.objects.filter(title="X").exists())

    def test_other_users_cannot_access_text(self):
        intruder = make_user("intruso")
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(self.a.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.post(reverse("library:text_delete", args=[self.a.pk])).status_code, 404)

    def test_pages_render(self):
        for url in [
            reverse("library:text_list"),
            reverse("library:text_create"),
            self.a.get_absolute_url(),
            reverse("library:text_update", args=[self.a.pk]),
            reverse("library:study", args=[self.a.pk]) + "?modo=audio_only",
            reverse("library:tag_list"),
        ]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(reverse("library:text_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])


class SimulatedTranslationTests(ProviderOverrideMixin, TestCase):
    def setUp(self):
        self.user = make_user()

    def test_records_provider_per_chunk(self):
        text = make_text(self.user)
        self.assertTrue(all(c.translation_provider == "mock" for c in text.chunks.all()))
        self.assertTrue(text.translation_is_simulated)

    def test_simulated_translation_becomes_stale_with_real_ai(self):
        text = make_text(self.user)
        with self.settings(AI_PROVIDER="mock"):
            self.assertFalse(Text.objects.get(pk=text.pk).translation_is_stale)
        with self.settings(AI_PROVIDER="anthropic"):
            fresh = Text.objects.get(pk=text.pk)
            self.assertTrue(fresh.translation_is_stale)

            class RealProvider(RecordingProvider):
                name = "anthropic"

            provider = self.use_provider(RealProvider())
            self.assertTrue(translate_text(fresh))
            self.assertEqual(len(provider.translate_calls), 1)
            fresh = Text.objects.get(pk=fresh.pk)
            self.assertFalse(fresh.translation_is_stale)
            self.assertFalse(fresh.translation_is_simulated)

    def test_detail_page_warns_about_simulated_translation(self):
        text = make_text(self.user)
        self.client.force_login(self.user)
        with self.settings(AI_PROVIDER="anthropic"):
            response = self.client.get(text.get_absolute_url())
        self.assertContains(response, "Traduzir com IA agora")


class AudioTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.text = make_text(self.user)
        self.chunk = self.text.chunks.first()
        import tempfile

        self.media = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.media, True)

    def test_audio_generated_once_and_cached(self):
        from apps.library.services import tts

        calls = []

        def fake_synth(text, language, destination):
            calls.append(language)
            with open(destination, "wb") as fh:
                fh.write(b"ID3fake-mp3")

        with self.settings(TTS_PROVIDER="gtts", MEDIA_ROOT=self.media):
            first = tts.get_chunk_audio(self.chunk, synthesize=fake_synth)
            second = tts.get_chunk_audio(self.chunk, synthesize=fake_synth)
        self.assertEqual(first, second)
        self.assertEqual(calls, ["en"])
        self.assertEqual(first.read_bytes(), b"ID3fake-mp3")

    def test_audio_failure_raises_tts_error(self):
        from apps.library.services import tts

        def broken(text, language, destination):
            raise OSError("sem rede")

        with self.settings(TTS_PROVIDER="gtts", MEDIA_ROOT=self.media):
            with self.assertRaises(tts.TTSError):
                tts.get_chunk_audio(self.chunk, synthesize=broken)
            self.assertFalse(tts.audio_path(self.chunk).exists())

    def test_audio_view_serves_mp3_to_owner_only(self):
        from apps.library.services import tts

        with self.settings(TTS_PROVIDER="gtts", MEDIA_ROOT=self.media):
            path = tts.audio_path(self.chunk)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"ID3cached")
            url = reverse("library:chunk_audio", args=[self.chunk.pk])
            self.client.force_login(self.user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "audio/mpeg")
            self.assertEqual(b"".join(response.streaming_content), b"ID3cached")
            self.client.force_login(make_user("outro"))
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_audio_view_disabled_returns_503(self):
        self.client.force_login(self.user)
        with self.settings(TTS_PROVIDER="browser"):
            response = self.client.get(reverse("library:chunk_audio", args=[self.chunk.pk]))
        self.assertEqual(response.status_code, 503)

    def test_player_on_text_page_and_audio_urls(self):
        self.client.force_login(self.user)
        with self.settings(TTS_PROVIDER="gtts"):
            response = self.client.get(self.text.get_absolute_url())
        self.assertContains(response, "data-tts-player")
        self.assertContains(response, reverse("library:chunk_audio", args=[self.chunk.pk]))


class DeleteTextTests(TestCase):
    def test_delete_buttons_and_flow(self):
        user = make_user()
        text = make_text(user)
        self.client.force_login(user)
        delete_url = reverse("library:text_delete", args=[text.pk])
        self.assertContains(self.client.get(reverse("library:text_list")), delete_url)
        self.assertContains(self.client.get(reverse("library:text_update", args=[text.pk])), delete_url)
        self.assertContains(self.client.get(delete_url), "Excluir definitivamente")
        response = self.client.post(delete_url)
        self.assertRedirects(response, reverse("library:text_list"))
        self.assertFalse(Text.objects.filter(pk=text.pk).exists())
