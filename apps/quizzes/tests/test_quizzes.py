from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.ai import AIServiceError, GeneratedQuiz, SkippedKind, set_provider_override
from apps.core.tests.factories import (
    FailingProvider,
    RecordingProvider,
    ScriptedProvider,
    make_text,
    make_user,
    question,
    quiz_of,
)
from apps.quizzes import services
from apps.quizzes.models import AttemptAnswer, Option, Question, Quiz, QuizAttempt


def answer_all(quiz, correct_count):
    """Seleções com `correct_count` respostas corretas."""
    selections = {}
    for i, question in enumerate(quiz.questions.prefetch_related("options")):
        options = list(question.options.all())
        right = next(o for o in options if o.is_correct)
        wrong = next(o for o in options if not o.is_correct)
        selections[question.pk] = (right if i < correct_count else wrong).pk
    return selections


ALL_KINDS_PLAN = {"fill_blank": 2, "comprehension": 2, "listening": 1, "synonym_antonym": 1, "word_order": 1, "find_error": 1}


class QuizGenerationTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.text = make_text(self.user)
        self.addCleanup(set_provider_override, None)

    def assert_five_options_one_correct(self, quiz):
        for q in quiz.questions.prefetch_related("options"):
            options = list(q.options.all())
            self.assertEqual(len(options), 5, q.prompt)
            self.assertEqual(sum(o.is_correct for o in options), 1, q.prompt)
            self.assertEqual([o.letter for o in options], ["A", "B", "C", "D", "E"])

    def test_quick_quiz_uses_default_plan(self):
        quiz = services.generate_quiz(self.text)
        self.assertEqual(quiz.questions.count(), sum(settings.QUIZ_DEFAULT_PLAN.values()))
        self.assertEqual(quiz.options_per_question, 5)
        self.assert_five_options_one_correct(quiz)
        self.assertEqual(quiz.config["plan"], settings.QUIZ_DEFAULT_PLAN)

    def test_all_six_kinds_combined_in_one_quiz(self):
        quiz = services.generate_quiz(self.text, plan=ALL_KINDS_PLAN, title="Revisão")
        kinds = {}
        for kind in quiz.questions.values_list("kind", flat=True):
            kinds[kind] = kinds.get(kind, 0) + 1
        self.assertEqual(kinds, ALL_KINDS_PLAN)
        self.assert_five_options_one_correct(quiz)
        self.assertEqual(quiz.title, "Revisão")
        self.assertEqual(quiz.label, "Quiz #1 · Revisão")
        # perguntas agrupadas por tipo, na ordem do plano
        order = list(quiz.questions.values_list("kind", flat=True))
        self.assertEqual(order, sorted(order, key=list(ALL_KINDS_PLAN).index))
        listening = quiz.questions.get(kind="listening")
        self.assertIn(listening.passage, self.text.content)
        self.assertEqual(quiz.questions.exclude(kind="listening").exclude(passage="").count(), 0)

    def test_each_kind_individually(self):
        for kind in ALL_KINDS_PLAN:
            quiz = services.generate_quiz(self.text, plan={kind: 1})
            self.assertEqual(list(quiz.questions.values_list("kind", flat=True)), [kind], kind)
            self.assert_five_options_one_correct(quiz)

    def test_multiple_independent_quizzes_with_versions(self):
        first = services.generate_quiz(self.text)
        attempt = services.start_attempt(first, self.user, "reading")
        services.submit_attempt(attempt, answer_all(first, 3))
        second = services.generate_quiz(self.text, plan={"word_order": 2})
        third = services.generate_quiz(self.text, plan=ALL_KINDS_PLAN)
        self.assertEqual([q.version for q in self.text.quizzes.order_by("version")], [1, 2, 3])
        first.refresh_from_db()
        self.assertEqual(first.questions.count(), 5)
        self.assertEqual(QuizAttempt.objects.get(pk=attempt.pk).score, 60)
        self.assertEqual(second.questions.count(), 2)
        self.assertEqual(third.questions.count(), 8)

    def test_previous_questions_and_answers_are_sent_to_ai(self):
        provider = RecordingProvider()
        set_provider_override(provider)
        first = services.generate_quiz(self.text)
        services.generate_quiz(self.text)
        self.assertEqual(provider.quiz_calls[0].avoid_questions, ())
        sent = " ".join(provider.quiz_calls[-1].avoid_questions)
        for q in first.questions.all():
            self.assertIn(q.prompt, sent)
            self.assertIn(q.correct_option.text, sent)
        request = provider.quiz_calls[-1]
        self.assertEqual(request.num_options, 5)
        self.assertEqual(request.explanation_language.code, "pt")
        self.assertTrue(all(item["instructions"] for item in request.plan))

    def test_new_quiz_avoids_repeating_previous_questions(self):
        first = services.generate_quiz(self.text, plan=ALL_KINDS_PLAN)
        second = services.generate_quiz(self.text, plan=ALL_KINDS_PLAN)
        older = [(q.prompt, q.correct_option.text, q.passage) for q in first.questions.all()]
        mine = [(q.prompt, q.correct_option.text, q.passage) for q in second.questions.all()]
        repeats = sum(1 for m in mine if any(services.same_question(m, o) for o in older))
        self.assertEqual(repeats, 0, first.questions.values_list("prompt", flat=True))

    def test_only_invalid_questions_are_regenerated(self):
        good = [question("Onde Maria trabalha?"), question("Como ela vai ao escritório?")]
        bad = question("Pergunta com quatro alternativas?", options=["a", "b", "c", "d"])
        replacement = question("Por que Maria estuda japonês?")
        provider = ScriptedProvider([
            GeneratedQuiz(questions=good + [bad], model="scripted"),
            GeneratedQuiz(questions=[replacement], model="scripted"),
        ])
        set_provider_override(provider)
        quiz = services.generate_quiz(self.text, plan={"comprehension": 3})
        self.assertEqual(provider.calls, 2)
        self.assertEqual(provider.requests[1].plan[0]["count"], 1)  # pede só a que faltou
        self.assertEqual(quiz.questions.count(), 3)
        self.assertEqual(quiz.config["rejected"], 1)
        self.assertIn("Por que Maria estuda japonês?", quiz.questions.values_list("prompt", flat=True))
        self.assert_five_options_one_correct(quiz)

    def test_fails_when_no_valid_question_can_be_generated(self):
        bad = quiz_of(["Só quatro?"], options=["a", "b", "c", "d"])
        set_provider_override(ScriptedProvider([bad, bad, bad]))
        with self.assertRaises(services.QuizValidationError):
            services.generate_quiz(self.text, plan={"comprehension": 1})
        self.assertEqual(self.text.quizzes.count(), 0)

    def test_unsupported_kind_is_replaced_by_other_selected_kinds(self):
        provider = ScriptedProvider([
            GeneratedQuiz(
                questions=[question("Sobre a cidade de Maria?"), question("Sobre o trabalho de Maria?")],
                skipped=[SkippedKind("synonym_antonym", "O texto não tem palavras adequadas.")],
            ),
            GeneratedQuiz(questions=[question("Sobre o aplicativo lançado?"), question("Sobre o japonês?")]),
        ])
        set_provider_override(provider)
        quiz = services.generate_quiz(self.text, plan={"comprehension": 2, "synonym_antonym": 2})
        self.assertEqual(quiz.questions.count(), 4)
        self.assertEqual(set(quiz.questions.values_list("kind", flat=True)), {"comprehension"})
        self.assertIn("Sinônimos e antônimos", quiz.generation_notes)
        self.assertIn("substituída", quiz.generation_notes)
        self.assertEqual(provider.requests[1].plan, ({"kind": "comprehension", "count": 2,
                                                       "label": "Interpretação de texto",
                                                       "instructions": provider.requests[1].plan[0]["instructions"]},))

    def test_only_unsupported_kind_reports_limitation(self):
        set_provider_override(ScriptedProvider([
            GeneratedQuiz(skipped=[SkippedKind("synonym_antonym", "Sem palavras adequadas.")]),
        ]))
        with self.assertRaises(services.QuizValidationError) as ctx:
            services.generate_quiz(self.text, plan={"synonym_antonym": 3})
        self.assertIn("Sem palavras adequadas", str(ctx.exception))

    def test_partial_quiz_is_saved_with_note(self):
        provider = ScriptedProvider([quiz_of(["Única pergunta válida?"])] * 3)
        set_provider_override(provider)
        quiz = services.generate_quiz(self.text, plan={"comprehension": 3})
        self.assertEqual(quiz.questions.count(), 1)
        self.assertIn("Foram geradas 1 de 3", quiz.generation_notes)

    def test_ai_failure_raises_service_error(self):
        set_provider_override(FailingProvider())
        with self.assertRaises(AIServiceError):
            services.generate_quiz(self.text)
        self.assertEqual(self.text.quizzes.count(), 0)

    def test_statement_language_is_configurable(self):
        quiz = services.generate_quiz(self.text, plan={"fill_blank": 1}, language="pt")
        self.assertEqual(quiz.language, "pt")
        self.assertTrue(quiz.questions.first().prompt.startswith("Escolha a alternativa"))

    def test_plan_limits(self):
        with self.assertRaises(services.QuizValidationError):
            services.generate_quiz(self.text, plan={})
        with self.assertRaises(services.QuizValidationError):
            services.generate_quiz(self.text, plan={"comprehension": settings.QUIZ_MAX_PER_KIND + 1})
        with self.assertRaises(services.QuizValidationError):
            services.generate_quiz(self.text, plan={k: settings.QUIZ_MAX_PER_KIND for k in ALL_KINDS_PLAN})
        self.assertEqual(services.validate_plan({"comprehension": "2", "x": 3, "word_order": 0}), {"comprehension": 2})


class QuestionValidationTests(TestCase):
    TEXT = "Maria works in Lisbon. She takes the tram every morning. Her team builds useful tools."

    def errors(self, q):
        services.clean_question(q)
        return services.validation_errors(q, num_options=5, text=self.TEXT)

    def test_structure(self):
        self.assertEqual(self.errors(question("Ok?")), [])
        self.assertTrue(self.errors(question("Quatro?", options=["a", "b", "c", "d"])))
        self.assertTrue(self.errors(question("Seis?", options=["a", "b", "c", "d", "e", "f"])))
        self.assertTrue(self.errors(question("Repetidas?", options=["a", "A", "c", "d", "e"])))
        self.assertTrue(self.errors(question("Vazia?", options=["a", "", "c", "d", "e"])))
        self.assertTrue(self.errors(question("Índice?", correct=5)))
        self.assertTrue(self.errors(question("Índice?", correct=True)))
        self.assertTrue(self.errors(question("")))
        self.assertTrue(self.errors(question("Tipo?", kind="essay")))

    def test_letter_prefixes_are_cleaned(self):
        q = question("Prefixos?", options=["A) one", "B) two", "C) three", "D) four", "E) five"])
        self.assertEqual(self.errors(q), [])
        self.assertEqual(q.options, ["one", "two", "three", "four", "five"])

    def test_fill_blank_requires_blank(self):
        self.assertTrue(self.errors(question("Maria works in Lisbon.", kind="fill_blank")))
        self.assertEqual(self.errors(question("Maria _____ in Lisbon.", kind="fill_blank")), [])

    def test_word_order_options_must_use_same_words(self):
        same = ["She takes the tram.", "The tram she takes.", "Takes she the tram.", "Tram the takes she.", "She the tram takes."]
        self.assertEqual(self.errors(question("Ordene: tram / She / the / takes", kind="word_order", options=same)), [])
        other = same[:4] + ["She takes a bus."]
        self.assertTrue(self.errors(question("Ordene: tram / She / the / takes", kind="word_order", options=other)))

    def test_listening_passage_must_come_from_text(self):
        self.assertTrue(self.errors(question("O que ouviu?", kind="listening", passage="")))
        self.assertTrue(self.errors(question("O que ouviu?", kind="listening", passage="Paris is beautiful in spring.")))
        ok = question("O que ouviu?", kind="listening", passage="She takes the tram every morning.")
        self.assertEqual(self.errors(ok), [])
        # pequenas diferenças são corrigidas para a frase exata do texto
        near = question("O que ouviu?", kind="listening", passage="She takes the tram every mornings.")
        self.assertEqual(self.errors(near), [])
        self.assertEqual(near.passage, "She takes the tram every morning.")

    def test_passage_is_cleared_for_other_kinds(self):
        q = question("Pergunta?", passage="Maria works in Lisbon.")
        self.assertEqual(self.errors(q), [])
        self.assertEqual(q.passage, "")

    def test_same_statement_with_different_answers_is_not_a_duplicate(self):
        q1 = question("Qual frase está INCORRETA?", kind="find_error", options=["a1", "b1", "c1", "d1", "e1"])
        q2 = question("Qual frase está INCORRETA?", kind="find_error", options=["a2", "b2", "c2", "d2", "e2"])
        services.validate_generated(GeneratedQuiz(questions=[q1, q2]))
        q3 = question("Qual frase está INCORRETA?", kind="find_error", options=["a1", "x", "y", "z", "w"])
        with self.assertRaises(services.QuizValidationError):
            services.validate_generated(GeneratedQuiz(questions=[q1, q3]))

    def test_database_enforces_single_correct_option(self):
        user = make_user()
        quiz = services.generate_quiz(make_text(user))
        question_obj = quiz.questions.first()
        wrong = question_obj.options.filter(is_correct=False).first()
        wrong.is_correct = True
        with self.assertRaises(IntegrityError), transaction.atomic():
            wrong.save()


class QuizBuilderViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.text = make_text(self.user)
        self.client.force_login(self.user)
        self.url = reverse("quizzes:quiz_new", args=[self.text.pk])

    def post(self, **counts):
        data = {"title": "Meu quiz", "language": "pt", "mode": "audio_only"}
        for kind, n in counts.items():
            data[f"use_{kind}"] = "on"
            data[f"count_{kind}"] = n
        return data

    def test_form_lists_six_kinds(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for label in ["Complete a frase", "Interpretação de texto", "Compreensão auditiva",
                      "Sinônimos e antônimos", "Ordem correta das palavras", "Identifique o erro"]:
            self.assertContains(response, label)

    def test_creates_quiz_with_chosen_plan(self):
        response = self.client.post(self.url, self.post(comprehension=2, word_order=1, find_error=1))
        quiz = Quiz.objects.get()
        self.assertRedirects(response, quiz.get_absolute_url())
        self.assertEqual(quiz.title, "Meu quiz")
        self.assertEqual(quiz.config["plan"], {"comprehension": 2, "word_order": 1, "find_error": 1})
        self.assertEqual(quiz.questions.count(), 4)

    def test_generate_and_start(self):
        data = self.post(listening=2)
        data["start"] = "1"
        response = self.client.post(self.url, data)
        attempt = QuizAttempt.objects.get()
        self.assertRedirects(response, reverse("quizzes:attempt_take", args=[attempt.pk]))
        self.assertEqual(attempt.mode, "audio_only")
        page = self.client.get(response["Location"])
        self.assertContains(page, "Compreensão auditiva")
        self.assertContains(page, "data-speak")

    def test_count_without_checkbox_still_selects_kind(self):
        self.client.post(self.url, {"language": "pt", "count_fill_blank": 2})
        self.assertEqual(Quiz.objects.get().questions.filter(kind="fill_blank").count(), 2)

    def test_validation_errors(self):
        response = self.client.post(self.url, {"language": "pt"})
        self.assertContains(response, "Selecione pelo menos um tipo")
        response = self.client.post(self.url, self.post(comprehension=0))
        self.assertContains(response, "Informe quantas perguntas")
        too_many = {k: settings.QUIZ_MAX_PER_KIND for k in ALL_KINDS_PLAN}
        response = self.client.post(self.url, self.post(**too_many))
        self.assertContains(response, f"no máximo {settings.QUIZ_MAX_QUESTIONS} perguntas")
        self.assertEqual(Quiz.objects.count(), 0)

    def test_ai_failure_shows_error(self):
        set_provider_override(FailingProvider())
        self.addCleanup(set_provider_override, None)
        response = self.client.post(self.url, self.post(comprehension=1))
        self.assertContains(response, "Não foi possível gerar o quiz")
        self.assertEqual(Quiz.objects.count(), 0)

    def test_other_users_cannot_create(self):
        self.client.force_login(make_user("outro"))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_quick_generate_view_handles_failure(self):
        set_provider_override(FailingProvider())
        self.addCleanup(set_provider_override, None)
        response = self.client.post(reverse("quizzes:quiz_generate", args=[self.text.pk]), follow=True)
        self.assertContains(response, "Não foi possível gerar o quiz")


class ListeningAudioTests(TestCase):
    def setUp(self):
        import shutil
        import tempfile

        self.user = make_user()
        self.text = make_text(self.user)
        self.quiz = services.generate_quiz(self.text, plan={"listening": 1, "comprehension": 1})
        self.question = self.quiz.questions.get(kind="listening")
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)

    def test_audio_served_to_owner_only(self):
        from apps.library.services import tts

        with self.settings(TTS_PROVIDER="gtts", MEDIA_ROOT=self.media):
            path = tts._path_for(self.question.passage, self.text.source_language)
            path.parent.mkdir(parents=True)
            path.write_bytes(b"ID3passage")
            url = reverse("quizzes:question_audio", args=[self.question.pk])
            self.client.force_login(self.user)
            response = self.client.get(url)
            self.assertEqual(response["Content-Type"], "audio/mpeg")
            self.assertEqual(b"".join(response.streaming_content), b"ID3passage")
            self.client.force_login(make_user("outro"))
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_take_page_hides_reference_text_for_listening(self):
        attempt = services.start_attempt(self.quiz, self.user, "reading")
        self.client.force_login(self.user)
        with self.settings(TTS_PROVIDER="gtts"):
            page = self.client.get(reverse("quizzes:attempt_take", args=[attempt.pk]))
        self.assertContains(page, 'id="reference-text" class="hidden"')
        self.assertContains(page, "Mostrar texto")
        self.assertContains(page, reverse("quizzes:question_audio", args=[self.question.pk]))


class LegacyQuizTests(TestCase):
    """Quizzes criados antes dos tipos de pergunta (4 alternativas) continuam funcionando."""

    def test_four_option_quiz_still_works(self):
        user = make_user()
        text = make_text(user)
        quiz = Quiz.objects.create(text=text, version=1, language="pt", options_per_question=4,
                                   config={"plan": {"comprehension": 2}, "legacy": True})
        for i in range(2):
            q = Question.objects.create(quiz=quiz, order=i, prompt=f"Antiga {i}?")
            Option.objects.bulk_create(Option(question=q, order=j, text=f"op{j}", is_correct=j == 0) for j in range(4))
        attempt = services.start_attempt(quiz, user, "reading")
        attempt = services.submit_attempt(attempt, answer_all(quiz, 1))
        self.assertEqual((attempt.correct_count, attempt.wrong_count, attempt.score), (1, 1, 50))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("quizzes:quiz_detail", args=[quiz.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse("quizzes:attempt_result", args=[attempt.pk])), "Interpretação de texto")


class AttemptTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.text = make_text(self.user)
        self.quiz = services.generate_quiz(self.text)

    def test_scoring(self):
        attempt = services.start_attempt(self.quiz, self.user, "audio_only")
        attempt = services.submit_attempt(attempt, answer_all(self.quiz, 4))
        self.assertEqual((attempt.correct_count, attempt.wrong_count, attempt.score), (4, 1, 80))
        self.assertEqual(attempt.status, QuizAttempt.Status.COMPLETED)
        self.assertEqual(attempt.text, self.text)
        self.assertEqual(attempt.mode, "audio_only")
        self.assertEqual(AttemptAnswer.objects.filter(attempt=attempt).count(), 5)

    def test_each_attempt_is_a_new_record(self):
        scores = []
        for correct in (2, 5, 0):
            attempt = services.start_attempt(self.quiz, self.user, "reading")
            scores.append(services.submit_attempt(attempt, answer_all(self.quiz, correct)).score)
        self.assertEqual(scores, [40, 100, 0])
        self.assertEqual(
            list(self.quiz.attempts.order_by("started_at").values_list("score", flat=True)), [40, 100, 0]
        )

    def test_completed_attempt_cannot_be_resubmitted(self):
        attempt = services.start_attempt(self.quiz, self.user, "reading")
        services.submit_attempt(attempt, answer_all(self.quiz, 1))
        with self.assertRaises(services.AttemptError):
            services.submit_attempt(attempt, answer_all(self.quiz, 5))
        attempt.refresh_from_db()
        self.assertEqual(attempt.score, 20)

    def test_all_questions_must_be_answered(self):
        attempt = services.start_attempt(self.quiz, self.user, "reading")
        selections = answer_all(self.quiz, 5)
        selections.pop(next(iter(selections)))
        with self.assertRaises(services.AttemptError):
            services.submit_attempt(attempt, selections)
        self.assertEqual(AttemptAnswer.objects.count(), 0)

    def test_option_from_another_question_is_rejected(self):
        attempt = services.start_attempt(self.quiz, self.user, "reading")
        selections = answer_all(self.quiz, 5)
        q1, q2 = list(selections)[:2]
        selections[q1] = Option.objects.filter(question_id=q2).first().pk
        with self.assertRaises(services.AttemptError):
            services.submit_attempt(attempt, selections)

    def test_quiz_stats_annotation(self):
        for correct in (1, 4, 3):
            attempt = services.start_attempt(self.quiz, self.user, "reading")
            services.submit_attempt(attempt, answer_all(self.quiz, correct))
        services.start_attempt(self.quiz, self.user, "reading")  # em andamento: não conta
        quiz = services.quizzes_with_stats(self.text, self.user).get(pk=self.quiz.pk)
        self.assertEqual((quiz.attempt_count, quiz.best_score, quiz.last_score), (3, 80, 60))

    def test_abandoned_attempts(self):
        attempt = services.start_attempt(self.quiz, self.user, "reading")
        QuizAttempt.objects.filter(pk=attempt.pk).update(started_at=timezone.now() - timedelta(days=2))
        self.assertEqual(services.mark_abandoned_attempts(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, QuizAttempt.Status.ABANDONED)

    def test_full_flow_through_views(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("quizzes:attempt_start", args=[self.quiz.pk]), {"mode": "audio_only"})
        attempt = QuizAttempt.objects.get()
        self.assertRedirects(response, reverse("quizzes:attempt_take", args=[attempt.pk]))
        page = self.client.get(response["Location"])
        self.assertNotContains(page, self.text.chunks.first().content[:40] + "</p>")  # texto oculto na interface
        self.assertContains(page, "chunk-texts")  # mas disponível para o áudio
        data = {f"q{qid}": oid for qid, oid in answer_all(self.quiz, 5).items()}
        response = self.client.post(reverse("quizzes:attempt_take", args=[attempt.pk]), data)
        self.assertRedirects(response, reverse("quizzes:attempt_result", args=[attempt.pk]))
        result = self.client.get(response["Location"])
        self.assertContains(result, "5 acertos")
        self.assertContains(result, "Explicação")
        for url in [reverse("quizzes:quiz_detail", args=[self.quiz.pk]), reverse("quizzes:attempt_history")]:
            self.assertEqual(self.client.get(url).status_code, 200)

    def test_attempts_are_private(self):
        attempt = services.start_attempt(self.quiz, self.user, "reading")
        self.client.force_login(make_user("outro"))
        self.assertEqual(self.client.get(reverse("quizzes:attempt_take", args=[attempt.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("quizzes:attempt_start", args=[self.quiz.pk])).status_code, 404)


class QuizDeleteTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.login(username=self.user.username, password="senha-forte-123")
        self.text = make_text(self.user)
        self.quiz = services.generate_quiz(self.text)
        self.other_quiz = services.generate_quiz(self.text)
        attempt = services.start_attempt(self.quiz, self.user, "reading")
        services.submit_attempt(attempt, answer_all(self.quiz, 3))
        services.start_attempt(self.quiz, self.user, "reading")
        self.url = reverse("quizzes:quiz_delete", args=[self.quiz.pk])

    def test_confirmation_page_shows_what_will_be_removed(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.context["attempt_count"], response.context["completed_count"]), (2, 1))
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())

    def test_delete_removes_quiz_questions_and_attempts_only(self):
        response = self.client.post(self.url, follow=True)
        self.assertRedirects(response, f"{self.text.get_absolute_url()}#quizzes")
        self.assertContains(response, f"{self.quiz.label} foi excluído.")
        self.assertFalse(Quiz.objects.filter(pk=self.quiz.pk).exists())
        self.assertFalse(Question.objects.filter(quiz_id=self.quiz.pk).exists())
        self.assertFalse(QuizAttempt.objects.filter(quiz_id=self.quiz.pk).exists())
        self.assertTrue(Quiz.objects.filter(pk=self.other_quiz.pk).exists())
        self.assertTrue(self.text.__class__.objects.filter(pk=self.text.pk).exists())

    def test_delete_buttons_are_shown(self):
        self.assertContains(self.client.get(self.text.get_absolute_url()), self.url)
        self.assertContains(self.client.get(self.quiz.get_absolute_url()), self.url)

    def test_other_users_cannot_delete(self):
        self.client.logout()
        other = make_user(username="outro")
        self.client.login(username=other.username, password="senha-forte-123")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertTrue(Quiz.objects.filter(pk=self.quiz.pk).exists())
