from datetime import timedelta

from django.http import QueryDict
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.tests.factories import make_text, make_user
from apps.dashboard import services
from apps.library.models import Tag
from apps.quizzes import services as quiz_services
from apps.quizzes.models import QuizAttempt
from apps.quizzes.tests.test_quizzes import answer_all


@override_settings(DASHBOARD_REVIEW_THRESHOLD=70, DASHBOARD_MIN_ATTEMPTS=2, DASHBOARD_PRIOR_WEIGHT=2)
class DashboardServiceTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.tech = Tag.objects.create(owner=self.user, name="Tecnologia")
        self.work = Tag.objects.create(owner=self.user, name="Trabalho")
        self.easy = make_text(self.user, title="Fácil", tags=[self.tech])
        self.hard = make_text(self.user, title="Difícil", tags=[self.tech, self.work])
        self.once = make_text(self.user, title="Uma vez", source="es", tags=[self.work])
        self.never = make_text(self.user, title="Nunca estudado")

    def attempt(self, text, correct, days_ago=0, quiz=None):
        quiz = quiz or text.quizzes.first() or quiz_services.generate_quiz(text)
        attempt = quiz_services.start_attempt(quiz, self.user, "reading")
        attempt = quiz_services.submit_attempt(attempt, answer_all(quiz, correct))
        if days_ago:
            QuizAttempt.objects.filter(pk=attempt.pk).update(completed_at=timezone.now() - timedelta(days=days_ago))
        return attempt

    def build(self, **params):
        query = QueryDict(mutable=True)
        for key, value in params.items():
            if isinstance(value, list):
                query.setlist(key, [str(v) for v in value])
            else:
                query[key] = str(value)
        return services.build_dashboard(self.user, services.parse_filters(query))

    def seed(self):
        for correct in (5, 5, 4):
            self.attempt(self.easy, correct)  # média 93
        for correct in (1, 2, 1, 2):
            self.attempt(self.hard, correct)  # média 30
        self.attempt(self.once, 0)  # uma única tentativa ruim

    def test_kpis_use_only_completed_attempts(self):
        self.seed()
        quiz_services.start_attempt(self.easy.quizzes.first(), self.user, "reading")  # em andamento
        quiz_services.generate_quiz(self.never)  # quiz gerado sem tentativas
        kpis = self.build()["kpis"]
        self.assertEqual(kpis["total_texts"], 4)
        self.assertEqual(kpis["total_quizzes"], 4)
        self.assertEqual(kpis["total_attempts"], 8)
        self.assertAlmostEqual(kpis["avg_correct"], 20 / 8)
        self.assertAlmostEqual(kpis["avg_wrong"], 20 / 8)
        self.assertAlmostEqual(kpis["avg_score"], 50)
        self.assertEqual(kpis["texts_studied"], 3)
        self.assertEqual(kpis["texts_review"], 2)

    def test_rankings_order_and_no_overlap(self):
        self.seed()
        data = self.build()
        best_titles = [r["text"].title for r in data["best"]]
        worst_titles = [r["text"].title for r in data["worst"]]
        self.assertEqual(best_titles, ["Fácil"])
        self.assertFalse(set(best_titles) & set(worst_titles))
        # O texto com 4 tentativas ruins vem antes do texto com uma única tentativa (0%)
        self.assertEqual(worst_titles, ["Difícil", "Uma vez"])
        once = data["worst"][1]
        self.assertTrue(once["low_confidence"])
        self.assertEqual(once["avg_score"], 0)

    def test_adjusted_score(self):
        self.assertAlmostEqual(services.adjusted_score(0, 1, 60, prior_weight=2), 40)
        self.assertAlmostEqual(services.adjusted_score(30, 4, 60, prior_weight=2), 40)
        self.assertAlmostEqual(services.adjusted_score(50, 10, None), 50)

    def test_tag_performance_counts_multi_tag_texts_in_each_tag(self):
        self.seed()
        tags = {r["tag"].name: r for r in self.build()["tags"] if r["tag"]}
        self.assertEqual(tags["Tecnologia"]["attempts"], 7)  # Fácil (3) + Difícil (4)
        self.assertEqual(tags["Trabalho"]["attempts"], 5)  # Difícil (4) + Uma vez (1)
        self.assertEqual(tags["Tecnologia"]["texts"], 2)
        # A soma por tag (12) é maior que o total real (8): por isso não somamos categorias.
        self.assertEqual(self.build()["kpis"]["total_attempts"], 8)

    def test_period_filter(self):
        self.attempt(self.easy, 5, days_ago=40)
        self.attempt(self.easy, 1)
        self.assertEqual(self.build(periodo="30")["kpis"]["total_attempts"], 1)
        self.assertEqual(self.build(periodo="all")["kpis"]["total_attempts"], 2)
        start = (timezone.localdate() - timedelta(days=45)).isoformat()
        end = (timezone.localdate() - timedelta(days=35)).isoformat()
        self.assertEqual(self.build(periodo="custom", de=start, ate=end)["kpis"]["total_attempts"], 1)

    def test_language_tag_and_text_filters(self):
        self.seed()
        self.assertEqual(self.build(lang="es")["kpis"]["total_attempts"], 1)
        self.assertEqual(self.build(tag=[self.tech.pk])["kpis"]["total_attempts"], 7)
        self.assertEqual(self.build(texto=self.hard.pk)["kpis"]["total_attempts"], 4)

    def test_new_attempt_updates_dashboard(self):
        self.attempt(self.easy, 1)
        self.assertEqual(self.build()["kpis"]["avg_score"], 20)
        self.attempt(self.easy, 5)
        self.assertEqual(self.build()["kpis"]["avg_score"], 60)

    def test_text_report(self):
        q1 = quiz_services.generate_quiz(self.easy)
        q2 = quiz_services.generate_quiz(self.easy)
        self.attempt(self.easy, 2, quiz=q1)
        self.attempt(self.easy, 4, quiz=q1)
        self.attempt(self.easy, 5, quiz=q2)
        report = services.text_report(self.user, self.easy)
        self.assertEqual(report["summary"]["attempts"], 3)
        self.assertEqual(report["summary"]["best"].score, 100)
        self.assertEqual(report["summary"]["worst"].score, 40)
        self.assertEqual(report["evolution"]["scores"], [40, 80, 100])
        self.assertEqual(report["comparison"]["avg_score"], [60.0, 100.0])


class DashboardViewTests(TestCase):
    def test_dashboard_pages_render(self):
        user = make_user()
        text = make_text(user)
        quiz = quiz_services.generate_quiz(text)
        quiz_services.submit_attempt(quiz_services.start_attempt(quiz, user, "reading"), answer_all(quiz, 3))
        self.client.force_login(user)
        for url in [
            reverse("dashboard:index"),
            reverse("dashboard:index") + "?periodo=7&lang=en",
            reverse("dashboard:index") + "?periodo=custom&de=2020-01-01&ate=bad",
            reverse("dashboard:text_report", args=[text.pk]),
            reverse("tracking:session_history"),
        ]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)
        self.assertContains(self.client.get(reverse("dashboard:index")), "Textos com mais acertos")

    def test_text_report_is_private(self):
        text = make_text(make_user())
        self.client.force_login(make_user("outro"))
        self.assertEqual(self.client.get(reverse("dashboard:text_report", args=[text.pk])).status_code, 404)
