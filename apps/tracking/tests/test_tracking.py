import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.tests.factories import make_text, make_user
from apps.tracking import services
from apps.tracking.models import AccessSession, StudySession

PASSWORD = "senha-forte-123"


def login(client, user):
    assert client.login(username=user.username, password=PASSWORD)


def shift(access, **fields):
    """Desloca timestamps de uma sessão para simular a passagem do tempo."""
    AccessSession.objects.filter(pk=access.pk).update(**fields)
    access.refresh_from_db()
    return access


@override_settings(
    SESSION_INACTIVITY_TIMEOUT_SECONDS=1800,
    SESSION_ABANDON_TIMEOUT_SECONDS=600,
    SESSION_ACTIVE_GAP_SECONDS=300,
    STUDY_MAX_CREDIT_SECONDS=60,
)
class AccessSessionFlowTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_login_starts_session(self):
        login(self.client, self.user)
        access = AccessSession.objects.get(user=self.user)
        self.assertTrue(access.is_active)
        self.assertEqual(self.client.session[services.SESSION_KEY], access.pk)
        self.assertIsNone(access.ended_at)

    def test_session_continues_across_pages_without_restarting(self):
        login(self.client, self.user)
        access = AccessSession.objects.get()
        started = access.started_at
        for url in [reverse("dashboard:index"), reverse("library:text_list"), reverse("library:tag_list")]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            timer = json.loads(response.content.decode().split('id="timer-config" type="application/json">')[1].split("</script>")[0])
            self.assertEqual(timer["started_at_ms"], int(started.timestamp() * 1000))
        self.assertEqual(AccessSession.objects.count(), 1)

    def test_timer_reference_survives_reload(self):
        login(self.client, self.user)
        access = shift(AccessSession.objects.get(), started_at=timezone.now() - timedelta(minutes=42))
        response = self.client.get(reverse("dashboard:index"))
        timer = response.context["timer"]
        elapsed = (timer["server_now_ms"] - timer["started_at_ms"]) / 1000
        self.assertAlmostEqual(elapsed, 42 * 60, delta=5)
        self.assertEqual(services.elapsed_seconds(access) // 60, 42)

    def test_logout_closes_session_and_stores_duration(self):
        login(self.client, self.user)
        access = shift(AccessSession.objects.get(), started_at=timezone.now() - timedelta(minutes=10))
        self.client.post(reverse("accounts:logout"))
        access.refresh_from_db()
        self.assertEqual(access.status, AccessSession.Status.CLOSED)
        self.assertEqual(access.end_reason, AccessSession.EndReason.LOGOUT)
        self.assertAlmostEqual(access.duration_seconds, 600, delta=5)
        self.assertGreaterEqual(access.ended_at, access.started_at)

    def test_history_is_preserved_across_logins(self):
        for _ in range(3):
            login(self.client, self.user)
            self.client.post(reverse("accounts:logout"))
        self.assertEqual(AccessSession.objects.filter(user=self.user, status="closed").count(), 3)

    def test_close_is_idempotent(self):
        access = services.start_access_session(self.user)
        end = access.started_at + timedelta(minutes=5)
        self.assertTrue(services.close_access_session(access, end, AccessSession.EndReason.LOGOUT))
        self.assertFalse(services.close_access_session(access, end + timedelta(hours=1), AccessSession.EndReason.LOGOUT))
        access.refresh_from_db()
        self.assertEqual(access.duration_seconds, 300)

    def test_inactivity_expires_session_on_server(self):
        login(self.client, self.user)
        past = timezone.now() - timedelta(minutes=45)
        access = shift(AccessSession.objects.get(), started_at=past, last_activity_at=past, last_seen_at=timezone.now())
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        access.refresh_from_db()
        self.assertEqual(access.end_reason, AccessSession.EndReason.INACTIVITY)
        # encerrada no fim do período de tolerância, não no momento da requisição
        self.assertAlmostEqual(access.duration_seconds, 1800, delta=2)
        # usuário foi desconectado
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 302)

    def test_heartbeat_after_expiry_returns_401(self):
        login(self.client, self.user)
        past = timezone.now() - timedelta(hours=1)
        shift(AccessSession.objects.get(), last_activity_at=past, last_seen_at=timezone.now())
        response = self.client.post(
            reverse("tracking:heartbeat"), json.dumps({"active": False}),
            content_type="application/json", HTTP_X_REQUESTED_WITH="fetch",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["status"], "expired")

    def test_abandoned_session_closed_at_last_signal(self):
        access = services.start_access_session(self.user)
        last_seen = timezone.now() - timedelta(minutes=30)
        shift(access, started_at=last_seen - timedelta(minutes=20), last_seen_at=last_seen, last_activity_at=last_seen)
        out = StringIO()
        call_command("close_stale_sessions", stdout=out)
        access.refresh_from_db()
        self.assertEqual(access.end_reason, AccessSession.EndReason.ABANDONED)
        self.assertEqual(access.ended_at, last_seen)
        self.assertEqual(access.duration_seconds, 20 * 60)
        self.assertIn("Sessões encerradas: 1", out.getvalue())

    def test_new_login_closes_previous_session_of_same_browser(self):
        login(self.client, self.user)
        first = AccessSession.objects.get()
        login(self.client, self.user)
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertEqual(AccessSession.objects.filter(status="active").count(), 1)

    def test_heartbeat_updates_activity_only_when_active(self):
        login(self.client, self.user)
        access = AccessSession.objects.get()
        before = timezone.now() - timedelta(minutes=2)
        shift(access, last_activity_at=before, last_seen_at=before)
        self.client.post(reverse("tracking:heartbeat"), json.dumps({"active": False}), content_type="application/json")
        access.refresh_from_db()
        self.assertEqual(access.last_activity_at, before)
        self.assertGreater(access.last_seen_at, before)
        self.client.post(reverse("tracking:heartbeat"), json.dumps({"active": True}), content_type="application/json")
        access.refresh_from_db()
        self.assertGreater(access.last_activity_at, before)
        self.assertGreaterEqual(access.active_seconds, 119)


@override_settings(SESSION_ACTIVE_GAP_SECONDS=300, STUDY_MAX_CREDIT_SECONDS=60, SESSION_HEARTBEAT_SECONDS=60)
class TimeAccountingTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.t0 = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)

    def test_active_time_ignores_long_gaps(self):
        access = services.start_access_session(self.user, now=self.t0)
        services.touch(access, self.t0 + timedelta(minutes=2))
        services.touch(access, self.t0 + timedelta(minutes=4))
        services.touch(access, self.t0 + timedelta(minutes=30))  # pausa longa: não conta
        services.touch(access, self.t0 + timedelta(minutes=31))
        access.refresh_from_db()
        self.assertEqual(access.active_seconds, 5 * 60)

    def test_connected_time_merges_overlapping_sessions(self):
        a = services.start_access_session(self.user, "a", now=self.t0)
        b = services.start_access_session(self.user, "b", now=self.t0 + timedelta(minutes=30))
        services.close_access_session(a, self.t0 + timedelta(minutes=60), "logout")
        services.close_access_session(b, self.t0 + timedelta(minutes=90), "logout")
        total = services.connected_seconds_on(self.user, timezone.localdate(self.t0), now=self.t0 + timedelta(hours=3))
        self.assertEqual(total, 90 * 60)  # não 120 minutos

    def test_closed_sessions_counted_once(self):
        a = services.start_access_session(self.user, "a", now=self.t0)
        services.close_access_session(a, self.t0 + timedelta(minutes=10), "logout")
        services.close_access_session(a, self.t0 + timedelta(minutes=50), "logout")
        total = services.connected_seconds_on(self.user, timezone.localdate(self.t0), now=self.t0 + timedelta(hours=3))
        self.assertEqual(total, 10 * 60)

    def test_study_beats_credit_active_time_only(self):
        study = services.start_study_session(self.user, "reading", now=self.t0)
        services.study_beat(study, self.t0 + timedelta(seconds=20), active=True)
        services.study_beat(study, self.t0 + timedelta(seconds=40), active=False)
        services.study_beat(study, self.t0 + timedelta(seconds=60), active=True)
        services.study_beat(study, self.t0 + timedelta(seconds=600), active=True)  # limitado a 60 s
        study.refresh_from_db()
        self.assertEqual(study.duration_seconds, 20 + 20 + 60)

    def test_parallel_study_tabs_are_not_double_counted(self):
        a = services.start_study_session(self.user, "reading", now=self.t0)
        b = services.start_study_session(self.user, "quiz", now=self.t0)
        for second in range(10, 121, 10):
            services.study_beat(a if second % 20 else b, self.t0 + timedelta(seconds=second), active=True)
            services.study_beat(b if second % 20 else a, self.t0 + timedelta(seconds=second), active=True)
        total = sum(StudySession.objects.values_list("duration_seconds", flat=True))
        self.assertLessEqual(total, 120)
        self.assertGreaterEqual(total, 110)

    def test_ended_study_session_no_longer_accumulates(self):
        study = services.start_study_session(self.user, "audio", now=self.t0)
        services.study_beat(study, self.t0 + timedelta(seconds=30), active=True, end=True)
        services.study_beat(study, self.t0 + timedelta(seconds=60), active=True)
        study.refresh_from_db()
        self.assertEqual(study.duration_seconds, 30)
        self.assertIsNotNone(study.ended_at)

    def test_time_summary(self):
        now = timezone.now()
        study = services.start_study_session(self.user, "reading", now=now - timedelta(seconds=50))
        services.study_beat(study, now - timedelta(seconds=10), active=True)
        access = services.start_access_session(self.user, now=now - timedelta(minutes=5))
        access = services.touch(access, now, activity=False)  # heartbeat recente
        summary = services.time_summary(self.user, now=now, current_access=access)
        self.assertEqual(summary["study_today"], 40)
        self.assertEqual(summary["study_week"], 40)
        self.assertEqual(summary["study_month"], 40)
        self.assertAlmostEqual(summary["current_session_seconds"], 300, delta=1)
        self.assertGreaterEqual(summary["connected_today"], 300)

    def test_study_endpoints(self):
        self.client.force_login(self.user)
        text = make_text(self.user)
        response = self.client.post(
            reverse("tracking:study_start"), json.dumps({"activity": "listening", "text_id": text.pk}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        beat = self.client.post(response.json()["beat_url"], json.dumps({"active": True}), content_type="application/json")
        self.assertEqual(beat.status_code, 200)
        bad = self.client.post(reverse("tracking:study_start"), json.dumps({"activity": "hack"}), content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        other = make_user("outro")
        self.client.force_login(other)
        stolen = self.client.post(response.json()["beat_url"], "{}", content_type="application/json")
        self.assertEqual(stolen.status_code, 404)


class SessionHistoryDeleteTests(TestCase):
    def setUp(self):
        self.user = make_user()
        login(self.client, self.user)
        self.current = AccessSession.objects.get(user=self.user)
        now = timezone.now()
        self.old = [
            services.start_access_session(self.user, now=now - timedelta(days=d)) for d in (1, 2)
        ]
        for access in self.old:
            services.close_access_session(access, access.started_at + timedelta(minutes=5), AccessSession.EndReason.LOGOUT)
        self.study = services.start_study_session(self.user, StudySession.Activity.READING, access=self.old[0])

    def test_delete_single_session_keeps_study_time(self):
        response = self.client.post(reverse("tracking:session_delete", args=[self.old[0].pk]))
        self.assertRedirects(response, reverse("tracking:session_history"))
        self.assertFalse(AccessSession.objects.filter(pk=self.old[0].pk).exists())
        self.study.refresh_from_db()
        self.assertIsNone(self.study.access_session)

    def test_cannot_delete_current_or_other_users_session(self):
        other = services.start_access_session(make_user(username="outro"))
        for pk in (self.current.pk, other.pk):
            self.assertEqual(self.client.post(reverse("tracking:session_delete", args=[pk])).status_code, 404)
        self.assertEqual(AccessSession.objects.filter(pk__in=[self.current.pk, other.pk]).count(), 2)

    def test_delete_requires_post(self):
        self.assertEqual(self.client.get(reverse("tracking:session_delete", args=[self.old[0].pk])).status_code, 405)

    def test_clear_history_keeps_current_session(self):
        other = services.start_access_session(make_user(username="outro"))
        self.client.post(reverse("tracking:session_clear"))
        self.assertEqual(list(AccessSession.objects.filter(user=self.user)), [self.current])
        self.assertTrue(AccessSession.objects.filter(pk=other.pk).exists())
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 200)
