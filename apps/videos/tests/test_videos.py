import json
import os
import shutil
import subprocess
import tempfile
from datetime import timedelta
from io import BytesIO
from unittest import skipUnless

from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.ai import set_provider_override
from apps.core.ai.mock_provider import MockProvider
from apps.core.http import ranged_file_response
from apps.core.tests.factories import make_user
from apps.quizzes.models import Question, Quiz
from apps.videos import asr, pipeline, services
from apps.videos.models import MediaSegment, MediaSource, ProcessingStep, TranscriptCue

PASSWORD = "senha-forte-123"
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
CHUNK = 64 * 1024


def make_video(path, seconds=7, audio=True):
    """Vídeo sintético: imagem de teste + tom com ruído rosa (ou sem faixa de áudio)."""
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-f", "lavfi", "-i",
                "anoisesrc=color=pink:amplitude=0.1", "-filter_complex", "[1][2]amix=inputs=2[a]", "-map", "0:v", "-map", "[a]",
                "-c:a", "aac"]
    cmd += ["-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True)
    return path


class FailingTranscriber:
    name = "falha"

    def transcribe(self, path, language):
        raise asr.ASRError("modelo indisponível")


class MediaTestCase(TestCase):
    """Base: MEDIA_ROOT temporário, usuário logado e transcritor simulado."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, True)
        overrides = override_settings(MEDIA_ROOT=self.media_root, AUDIO_SEGMENT_SECONDS=3,
                                      VIDEO_UPLOAD_CHUNK_BYTES=CHUNK, AUDIO_RNNOISE_MODEL="")
        overrides.enable()
        self.addCleanup(overrides.disable)
        asr.set_transcriber_override(asr.MockTranscriber())
        self.addCleanup(asr.set_transcriber_override, None)
        set_provider_override(MockProvider())
        self.addCleanup(set_provider_override, None)
        self.user = make_user()
        self.client.login(username=self.user.username, password=PASSWORD)

    def upload(self, path, *, title="Aula", chunk=CHUNK):
        """Faz o upload completo pelo protocolo de blocos e devolve o vídeo."""
        size = os.path.getsize(path)
        resp = self.client.post(reverse("videos:upload_start"), json.dumps({
            "title": title, "language": "en", "target_language": "pt", "filename": os.path.basename(path), "size": size,
        }), content_type="application/json")
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()
        with open(path, "rb") as fh:
            offset = 0
            while offset < size:
                body = fh.read(chunk)
                r = self.client.post(f"{data['chunk_url']}?offset={offset}", body, content_type="application/octet-stream")
                self.assertEqual(r.status_code, 200, r.content)
                offset += len(body)
        return MediaSource.objects.get(pk=data["id"])

    def processed(self, seconds=7):
        video = make_video(os.path.join(self.media_root, "entrada.mp4"), seconds)
        media = self.upload(video)
        self.assertTrue(pipeline.process(media))
        media.refresh_from_db()
        return media


class UploadTests(MediaTestCase):
    def start(self, **overrides):
        payload = {"title": "", "language": "en", "target_language": "pt", "filename": "aula.mp4", "size": 1000, **overrides}
        return self.client.post(reverse("videos:upload_start"), json.dumps(payload), content_type="application/json")

    def test_validations_before_upload(self):
        cases = [({"filename": "aula.exe"}, "Formato não suportado"), ({"size": 0}, "tamanho inválido"),
                 ({"size": 10**13}, "limite"), ({"duration": 3 * 3600 + 60}, "3 horas"),
                 ({"target_language": "en"}, "diferente")]
        for overrides, message in cases:
            resp = self.start(**overrides)
            self.assertEqual(resp.status_code, 400, overrides)
            self.assertIn(message, resp.json()["error"])
        self.assertFalse(MediaSource.objects.exists())

    def test_title_defaults_to_filename(self):
        self.start(filename="Entrevista com Ana.mp4")
        self.assertEqual(MediaSource.objects.get().title, "Entrevista com Ana")

    def test_chunks_must_follow_order_and_resume(self):
        data = self.start(size=3 * CHUNK).json()
        url = data["chunk_url"]
        first = os.urandom(CHUNK)
        self.assertEqual(self.client.post(f"{url}?offset=0", first, content_type="application/octet-stream").status_code, 200)
        # Bloco repetido ou fora de ordem: 409 com o ponto de retomada.
        resp = self.client.post(f"{url}?offset=0", first, content_type="application/octet-stream")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["received_bytes"], CHUNK)
        too_big = os.urandom(CHUNK + 1)
        self.assertEqual(self.client.post(f"{url}?offset={CHUNK}", too_big, content_type="application/octet-stream").status_code, 409)
        media = MediaSource.objects.get()
        self.assertEqual((media.received_bytes, media.status), (CHUNK, MediaSource.Status.UPLOADING))
        # A página do vídeo oferece a retomada.
        self.assertContains(self.client.get(media.get_absolute_url()), "Continuar envio")

    def test_complete_upload_is_queued_with_steps(self):
        path = os.path.join(self.media_root, "f.mp4")
        with open(path, "wb") as fh:
            fh.write(os.urandom(2 * CHUNK + 100))
        media = self.upload(path)
        self.assertEqual(media.status, MediaSource.Status.QUEUED)
        self.assertEqual(list(media.steps.values_list("name", flat=True)), ProcessingStep.Name.values)
        self.assertTrue(os.path.exists(pipeline.local_path(media.original.name)))
        self.assertFalse(os.path.exists(services.partial_path(media)))
        with open(path, "rb") as a, open(pipeline.local_path(media.original.name), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_other_users_cannot_send_chunks_or_see_status(self):
        data = self.start().json()
        self.client.logout()
        other = make_user(username="outro")
        self.client.login(username=other.username, password=PASSWORD)
        resp = self.client.post(f"{data['chunk_url']}?offset=0", b"x", content_type="application/octet-stream")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.client.get(reverse("videos:media_status"), {"ids": data["id"]}).json()["items"], [])
        self.assertEqual(self.client.get(reverse("videos:media_detail", args=[data["id"]])).status_code, 404)


class SegmentBoundsTests(SimpleTestCase):
    def test_bounds(self):
        self.assertEqual(pipeline.segment_bounds(7, 3), [(0, 3), (3, 6), (6, 7)])
        self.assertEqual(pipeline.segment_bounds(6.4, 3), [(0, 3), (3, 6.4)])  # sobra < 1 s vai para o anterior
        self.assertEqual(pipeline.segment_bounds(2, 3), [(0, 2)])
        self.assertEqual(len(pipeline.segment_bounds(3 * 3600, 300)), 36)

    def test_parse_time(self):
        self.assertEqual(services.parse_time("75"), 75)
        self.assertEqual(services.parse_time("1:15"), 75)
        self.assertEqual(services.parse_time("1:01:15"), 3675)
        for bad in ("", "abc", "1:75", "-3", "1:2:3:4"):
            self.assertIsNone(services.parse_time(bad), bad)

    def test_subtitle_formats(self):
        cues = [TranscriptCue(start_seconds=301.5, end_seconds=304.25, text="Hello there.")]
        self.assertIn("00:00:01.500 --> 00:00:04.250\nHello there.", services.to_vtt(cues, offset=300))
        self.assertIn("00:05:01,500 --> 00:05:04,250", services.to_srt(cues))


class RangedResponseTests(SimpleTestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.write(fd, bytes(range(256)) * 4)
        os.close(fd)
        self.addCleanup(os.remove, self.path)

    def get(self, header=None):
        request = RequestFactory().get("/", **({"HTTP_RANGE": header} if header else {}))
        return ranged_file_response(request, self.path, content_type="audio/mpeg")

    def test_full_and_partial(self):
        full = self.get()
        self.assertEqual((full.status_code, full["Content-Length"], full["Accept-Ranges"]), (200, "1024", "bytes"))
        part = self.get("bytes=10-19")
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part["Content-Range"], "bytes 10-19/1024")
        self.assertEqual(b"".join(part.streaming_content), bytes(range(10, 20)))
        tail = self.get("bytes=-4")
        self.assertEqual(b"".join(tail.streaming_content), bytes(range(252, 256)))
        self.assertEqual(self.get("bytes=5000-").status_code, 416)


@skipUnless(HAS_FFMPEG, "FFmpeg não instalado")
class PipelineTests(MediaTestCase):
    def test_full_processing(self):
        media = self.processed(seconds=7)
        self.assertEqual(media.status, MediaSource.Status.READY)
        self.assertEqual(media.progress, 100)
        self.assertAlmostEqual(media.duration_seconds, 7, delta=0.2)
        self.assertTrue(all(s.status == ProcessingStep.Status.DONE and s.attempts == 1 for s in media.steps.all()))
        # Pronto: o vídeo enviado é descartado e ficam só os segmentos (áudio tratado).
        self.assertFalse(media.original)
        folder = pipeline.local_path(f"{pipeline.media_dir(media)}/.keep").rsplit("/", 1)[0]
        kept = [f for _, _, files in os.walk(folder) for f in files if f != ".keep"]
        self.assertTrue(kept)
        self.assertTrue(all(f.endswith(".mp3") for f in kept), kept)
        self.assertEqual(len(kept), media.segments.count())
        segments = list(media.segments.all())
        self.assertEqual([s.number for s in segments], [1, 2, 3])
        self.assertEqual([(s.start_seconds, round(s.end_seconds)) for s in segments], [(0, 3), (3, 6), (6, 7)])
        self.assertEqual(len({s.uid for s in segments}), 3)
        for seg in segments:
            self.assertEqual(seg.transcript_status, MediaSegment.TranscriptStatus.DONE)
            self.assertTrue(os.path.getsize(pipeline.local_path(seg.audio.name)) > 0)
            for cue in seg.cues.all():  # tempos absolutos dentro do segmento
                self.assertGreaterEqual(cue.start_seconds, seg.start_seconds)
                self.assertLessEqual(cue.end_seconds, seg.end_seconds + 0.01)
        self.assertFalse(os.path.exists(pipeline.work_path(media, "extracted.wav")))  # intermediários removidos

    def test_video_without_audio_fails_at_validation(self):
        media = self.upload(make_video(os.path.join(self.media_root, "mudo.mp4"), 2, audio=False))
        self.assertFalse(pipeline.process(media))
        media.refresh_from_db()
        self.assertEqual(media.status, MediaSource.Status.FAILED)
        self.assertIn("não tem faixa de áudio", media.error_message)
        self.assertEqual(media.steps.get(name="probe").status, ProcessingStep.Status.FAILED)

    @override_settings(VIDEO_MAX_DURATION_SECONDS=3)
    def test_video_over_limit_fails(self):
        media = self.upload(make_video(os.path.join(self.media_root, "longo.mp4"), 5))
        self.assertFalse(pipeline.process(media))
        media.refresh_from_db()
        self.assertIn("limite", media.error_message)

    def test_retry_resumes_from_failed_step_without_new_upload(self):
        asr.set_transcriber_override(FailingTranscriber())
        media = self.upload(make_video(os.path.join(self.media_root, "v.mp4"), 4))
        self.assertFalse(pipeline.process(media))
        media.refresh_from_db()
        self.assertEqual(media.status, MediaSource.Status.FAILED)
        self.assertEqual(media.steps.get(name="transcribe").status, ProcessingStep.Status.FAILED)
        self.assertEqual(media.segments.filter(transcript_status="failed").count(), 2)

        asr.set_transcriber_override(asr.MockTranscriber())
        response = self.client.post(reverse("videos:media_retry", args=[media.pk]))
        self.assertRedirects(response, media.get_absolute_url())
        media.refresh_from_db()
        self.assertEqual(media.status, MediaSource.Status.QUEUED)
        claimed = pipeline.claim_next()
        self.assertEqual(claimed.pk, media.pk)
        self.assertIsNone(pipeline.claim_next())  # não é reservado duas vezes
        self.assertTrue(pipeline.process(claimed))
        attempts = dict(media.steps.values_list("name", "attempts"))
        self.assertEqual(attempts, {"probe": 1, "extract": 1, "clean": 1, "segment": 1, "transcribe": 2})

    def test_stale_processing_goes_back_to_queue(self):
        media = self.upload(make_video(os.path.join(self.media_root, "v.mp4"), 2))
        pipeline.claim_next()
        media.steps.filter(name="probe").update(status=ProcessingStep.Status.RUNNING)
        MediaSource.objects.filter(pk=media.pk).update(heartbeat_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(pipeline.recover_stale(), 1)
        media.refresh_from_db()
        self.assertEqual(media.status, MediaSource.Status.QUEUED)
        self.assertEqual(media.steps.get(name="probe").status, ProcessingStep.Status.PENDING)


@skipUnless(HAS_FFMPEG, "FFmpeg não instalado")
class MediaViewsTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.media = self.processed(seconds=7)
        self.segment = self.media.segments.get(index=1)

    def test_pages_render(self):
        self.assertContains(self.client.get(reverse("videos:media_list")), self.media.title)
        detail = self.client.get(self.media.get_absolute_url())
        self.assertContains(detail, "Histórico do processamento")
        self.assertContains(detail, "Segmento 3")
        page = self.client.get(self.segment.get_absolute_url())
        self.assertContains(page, "Simulated sentence number 1.")
        self.assertEqual(page.context["cue_data"][0]["start"], 0.0)  # relativo ao segmento

    def test_audio_supports_range_and_download(self):
        url = reverse("videos:segment_audio", args=[self.media.pk, 1])
        partial = self.client.get(url, HTTP_RANGE="bytes=0-99")
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial["Content-Type"], "audio/mpeg")
        download = self.client.get(url, {"baixar": "1"})
        self.assertIn("attachment", download["Content-Disposition"])

    def test_subtitles(self):
        vtt = self.client.get(reverse("videos:segment_subtitles", args=[self.media.pk, 1, "vtt"])).content.decode()
        self.assertTrue(vtt.startswith("WEBVTT"))
        self.assertIn("00:00:00.000 -->", vtt)  # segmento 2 começa em 3 s no vídeo, 0 no próprio áudio
        full = self.client.get(reverse("videos:media_subtitles", args=[self.media.pk, "srt"])).content.decode()
        self.assertIn("00:00:03,000 -->", full)  # tempos do vídeo original
        data = json.loads(self.client.get(reverse("videos:media_subtitles", args=[self.media.pk, "json"])).content)
        seg = data["segments"][1]
        self.assertEqual((seg["number"], seg["start_seconds"]), (2, 3.0))
        self.assertEqual(seg["cues"][0]["relative_start_seconds"], 0.0)
        self.assertEqual(seg["cues"][0]["start_seconds"], 3.0)

    def test_review_transcript(self):
        cue = self.segment.cues.first()
        url = reverse("videos:segment_review", args=[self.media.pk, 1])
        self.client.post(url, {f"cue-{cue.pk}": "  Texto   corrigido. "})
        cue.refresh_from_db()
        self.assertEqual(cue.text, "Texto corrigido.")
        self.assertTrue(cue.edited)
        self.client.post(url, {f"cue-{cue.pk}": "   "})  # apagar volta ao reconhecido
        cue.refresh_from_db()
        self.assertFalse(cue.edited)
        self.client.post(url, {f"cue-{cue.pk}": "Outra coisa."})
        self.client.post(url, {"action": "restore"})
        cue.refresh_from_db()
        self.assertEqual(cue.text, cue.original_text)

    def test_retranscribe_queues_only_that_segment(self):
        self.client.post(reverse("videos:segment_retranscribe", args=[self.media.pk, 1]))
        self.media.refresh_from_db()
        self.assertEqual(self.media.status, MediaSource.Status.QUEUED)
        self.assertEqual(list(self.media.segments.filter(transcript_status="pending").values_list("index", flat=True)), [1])
        self.assertTrue(pipeline.process(pipeline.claim_next()))
        self.assertEqual(self.media.segments.filter(transcript_status="done").count(), 3)

    def test_access_is_private(self):
        self.client.logout()
        other = make_user(username="outro")
        self.client.login(username=other.username, password=PASSWORD)
        urls = [self.media.get_absolute_url(), self.segment.get_absolute_url(),
                reverse("videos:segment_audio", args=[self.media.pk, 1]),
                reverse("videos:media_subtitles", args=[self.media.pk, "json"])]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_delete_removes_files(self):
        folder = os.path.dirname(os.path.dirname(pipeline.local_path(self.segment.audio.name)))
        self.assertTrue(os.path.isdir(folder))
        self.client.post(reverse("videos:media_delete", args=[self.media.pk]))
        self.assertFalse(MediaSource.objects.exists())
        self.assertFalse(os.path.exists(folder))


@skipUnless(HAS_FFMPEG, "FFmpeg não instalado")
class QuizIntegrationTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.media = self.processed(seconds=7)

    def create(self, **data):
        return self.client.post(reverse("videos:media_quiz_create", args=[self.media.pk]), {"preset": "listening", **data})

    def test_sources(self):
        text_all, created = services.text_for_source(self.media, "all")
        self.assertTrue(created)
        self.assertEqual((text_all.source_start, round(text_all.source_end)), (0.0, 7))
        self.assertIn("Simulated sentence number 1.", text_all.content)
        self.assertEqual(services.text_for_source(self.media, "all"), (text_all, False))  # reaproveitado
        seg_text, _ = services.text_for_source(self.media, "segment:2")
        self.assertEqual(seg_text.source_segment.number, 3)
        rng_text, _ = services.text_for_source(self.media, "range:3-6")
        self.assertEqual((rng_text.source_start, rng_text.source_end), (3.0, 6.0))
        for bad in ("segment:9", "range:5-2", "xpto"):
            with self.assertRaises(services.SourceError):
                services.text_for_source(self.media, bad)

    def test_quiz_from_segment_is_ready_with_real_audio(self):
        response = self.create(source="segment:0", title="Áudio real")
        quiz = Quiz.objects.get()
        self.assertRedirects(response, quiz.get_absolute_url())
        self.assertEqual(quiz.text.source_media, self.media)
        listening = quiz.questions.filter(kind="listening")
        self.assertTrue(listening.exists())
        for q in listening:
            self.assertTrue(q.has_real_audio, q.passage)
            self.assertEqual(q.media_segment.index, 0)
            self.assertLessEqual(q.audio_end, 3.01)
        audio = self.client.get(reverse("quizzes:question_audio", args=[listening.first().pk]))
        self.assertEqual((audio.status_code, audio["Content-Type"]), (200, "audio/mpeg"))

    def test_video_quiz_can_be_answered_right_away(self):
        self.create(source="all")
        quiz = Quiz.objects.get()
        response = self.client.post(reverse("quizzes:attempt_start", args=[quiz.pk]), {"mode": "audio_only"})
        attempt = quiz.attempts.get()
        self.assertRedirects(response, attempt.get_absolute_url())

    def test_several_quizzes_from_same_video(self):
        self.create(source="all")
        self.create(source="all", preset="reading")
        self.create(source="segment:1")
        self.assertEqual(Quiz.objects.count(), 3)
        self.assertEqual(Quiz.objects.values("text").distinct().count(), 2)

    def test_range_source_and_errors(self):
        self.create(source="range", range_start="0:03", range_end="0:06")
        self.assertEqual(Quiz.objects.get().text.source_start, 3.0)
        response = self.create(source="range", range_start="", range_end="1:00")
        self.assertEqual(Quiz.objects.count(), 1)
        self.assertRedirects(response, self.media.get_absolute_url())

@skipUnless(HAS_FFMPEG, "FFmpeg não instalado")
class QuizAudioClipTests(MediaTestCase):
    def setUp(self):
        super().setUp()
        self.media = self.processed(seconds=7)
        self.client.post(reverse("videos:media_quiz_create", args=[self.media.pk]), {"preset": "listening", "source": "all"})
        self.quiz = Quiz.objects.get()
        self.questions = list(self.quiz.questions.filter(kind="listening"))
        self.assertTrue(self.questions)

    def clip_path(self, question):
        question.refresh_from_db()
        return pipeline.local_path(question.audio_clip.name)

    def test_clip_is_cut_from_treated_audio_when_quiz_is_created(self):
        for q in self.questions:
            q.refresh_from_db()
            self.assertTrue(q.audio_clip.name.startswith(f"quiz-audio/{self.user.pk}/{self.quiz.pk}/"))
            path = self.clip_path(q)
            self.assertGreater(os.path.getsize(path), 0)
            # Duração do trecho + folga, recortado do áudio tratado.
            from apps.videos import ffmpeg
            self.assertAlmostEqual(ffmpeg.probe(path).duration, q.audio_end - q.audio_start + 0.5, delta=0.3)

    def test_quiz_keeps_treated_audio_after_video_is_deleted(self):
        question = self.questions[0]
        path = self.clip_path(question)
        self.client.post(reverse("videos:media_delete", args=[self.media.pk]))
        self.assertFalse(MediaSource.objects.exists())
        question.refresh_from_db()
        self.assertIsNone(question.quiz.text.source_media)
        self.assertTrue(os.path.exists(path))
        audio = self.client.get(reverse("quizzes:question_audio", args=[question.pk]))
        self.assertEqual((audio.status_code, audio["Content-Type"]), (200, "audio/mpeg"))
        self.assertEqual(b"".join(audio.streaming_content if audio.streaming else [audio.content]),
                         open(path, "rb").read())

    def test_clip_is_removed_with_question_or_quiz(self):
        paths = [self.clip_path(q) for q in self.questions]
        self.client.post(reverse("quizzes:quiz_delete", args=[self.quiz.pk]))
        self.assertFalse(any(os.path.exists(p) for p in paths))

    def audio_bytes(self, question):
        resp = self.client.get(reverse("quizzes:question_audio", args=[question.pk]))
        self.assertEqual((resp.status_code, resp["Content-Type"]), (200, "audio/mpeg"))
        return b"".join(resp.streaming_content if resp.streaming else [resp.content])

    def remove_treated_audio(self, question):
        question.audio_clip.delete(save=True)
        MediaSegment.objects.filter(media=self.media).update(audio="")
        question.refresh_from_db()

    def test_clip_copies_treated_audio_without_reencoding(self):
        from unittest import mock

        from apps.videos import ffmpeg

        question = self.questions[0]
        question.audio_clip.delete(save=True)
        with mock.patch.object(ffmpeg, "run", wraps=ffmpeg.run) as run:
            path = services.question_clip(question)
        args = run.call_args.args[0]
        source = args[args.index("-i") + 1]
        # Recortado dos MP3 dos segmentos (ou da lista deles, se cruzar a divisa).
        self.assertTrue("/segments/" in source or "concat" in args, args)
        self.assertEqual(args[args.index("-c:a") + 1], "copy")
        self.assertNotIn("libmp3lame", args)
        # Pedidos seguintes reutilizam o recorte guardado, sem chamar o FFmpeg.
        with mock.patch.object(ffmpeg, "run") as run:
            self.assertEqual(services.question_clip(question), path)
            self.assertEqual(self.audio_bytes(question), open(path, "rb").read())
        run.assert_not_called()

    def test_clip_across_segments_joins_both(self):
        from apps.videos import ffmpeg

        # Segmentos de 3 s: o trecho de 2 s a 4 s cruza a divisa entre o 1º e o 2º.
        path = services._treated_clip(self.media, 2.0, 4.0, f"quiz-audio/teste/divisa.mp3")
        self.assertAlmostEqual(ffmpeg.probe(path).duration, 2.0 + 2 * services.CLIP_PADDING_SECONDS, delta=0.15)
        self.assertFalse([f for f in os.listdir(os.path.dirname(path)) if f.endswith(".txt")])

    def test_treated_audio_survives_new_attempts(self):
        question = self.questions[0]
        before = self.audio_bytes(question)
        for _ in range(2):  # acessar o quiz de novo mantém o mesmo áudio
            attempt = self.client.post(reverse("quizzes:attempt_start", args=[self.quiz.pk]), follow=True)
            self.assertContains(attempt, "data-treated-audio")
            self.assertContains(attempt, reverse("quizzes:question_audio", args=[question.pk]))
            self.assertEqual(self.audio_bytes(question), before)

    def test_missing_treated_audio_is_reported_not_replaced(self):
        question = self.questions[0]
        self.remove_treated_audio(question)
        self.assertIsNone(services.question_clip(question))
        self.assertEqual(question.audio_status, Question.AUDIO_UNAVAILABLE)
        # Nem o áudio original nem a voz sintetizada entram em silêncio no lugar.
        resp = self.client.get(reverse("quizzes:question_audio", args=[question.pk]))
        self.assertEqual(resp.status_code, 404)
        self.assertIn("áudio tratado", resp.json()["error"])
        attempt = self.client.post(reverse("quizzes:attempt_start", args=[self.quiz.pk]), follow=True)
        self.assertContains(attempt, "O áudio tratado deste trecho do vídeo não está disponível")
        self.assertNotContains(attempt, reverse("quizzes:question_audio", args=[question.pk]))

    def test_generation_warns_when_treated_audio_is_missing(self):
        self.quiz.delete()  # o texto curto do teste não comporta um segundo quiz sem repetir perguntas
        MediaSegment.objects.filter(media=self.media).update(audio="")
        self.client.post(reverse("videos:media_quiz_create", args=[self.media.pk]), {"preset": "listening", "source": "all"})
        quiz = Quiz.objects.get()
        located = quiz.questions.filter(audio_start__isnull=False)
        self.assertTrue(located.exists())
        self.assertFalse(located.exclude(audio_clip="").exists())
        self.assertIn("sem o áudio tratado", quiz.generation_notes)

@skipUnless(HAS_FFMPEG, "FFmpeg não instalado")
@override_settings(TEXT_CHUNK_MAX_CHARS=40, TTS_PROVIDER="browser")
class TextChunkTreatedAudioTests(MediaTestCase):
    """O player do texto de referência toca o áudio tratado do vídeo, não a voz sintetizada."""

    def setUp(self):
        super().setUp()
        self.media = self.processed(seconds=14)
        self.text, _ = services.text_for_source(self.media, "all")
        self.chunks = list(self.text.chunks.all())
        self.assertGreater(len(self.chunks), 1)

    def audio(self, chunk):
        resp = self.client.get(reverse("library:chunk_audio", args=[chunk.pk]))
        body = b"".join(resp.streaming_content if resp.streaming else [resp.content])
        return resp, body

    def test_chunks_are_located_and_cut_from_treated_audio(self):
        previous_end = 0
        for chunk in self.chunks:
            self.assertTrue(chunk.has_real_audio, chunk.content)
            self.assertGreaterEqual(chunk.audio_start, previous_end - 4.01)  # pode repetir a fala dividida
            previous_end = chunk.audio_end
            self.assertTrue(chunk.audio_clip.name.startswith(f"text-audio/{self.user.pk}/{self.text.pk}/"))
            resp, body = self.audio(chunk)
            self.assertEqual((resp.status_code, resp["Content-Type"]), (200, "audio/mpeg"))
            self.assertEqual(body, open(pipeline.local_path(chunk.audio_clip.name), "rb").read())

    def test_player_uses_treated_audio_even_with_server_tts_off(self):
        self.client.post(reverse("videos:media_quiz_create", args=[self.media.pk]), {"preset": "listening", "source": "all"})
        quiz = Quiz.objects.get()
        page = self.client.post(reverse("quizzes:attempt_start", args=[quiz.pk]), {"mode": "audio_only"}, follow=True)
        self.assertContains(page, "data-treated-audio")
        self.assertEqual(page.context["chunk_audio_urls"],
                         [reverse("library:chunk_audio", args=[c.pk]) for c in self.chunks])

    def test_treated_audio_survives_video_deletion_and_edits(self):
        path = pipeline.local_path(self.chunks[0].audio_clip.name)
        self.client.post(reverse("videos:media_delete", args=[self.media.pk]))
        self.text.refresh_from_db()
        self.assertIsNone(self.text.source_media)
        # Editar o fim do texto mantém o áudio dos trechos que não mudaram.
        from apps.library.services.texts import sync_chunks

        self.text.content = self.text.content + " Extra line."
        self.text.save()
        sync_chunks(self.text)
        new_chunks = list(self.text.chunks.all())
        self.assertEqual([c.audio_clip.name for c in new_chunks[:-1]], [c.audio_clip.name for c in self.chunks[:-1]])
        first = new_chunks[0]
        resp, body = self.audio(first)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body, open(path, "rb").read())
        self.text.delete()
        self.assertFalse(os.path.exists(path))

    def test_missing_treated_audio_is_reported(self):
        chunk = self.chunks[0]
        chunk.audio_clip.delete(save=True)
        MediaSegment.objects.filter(media=self.media).update(audio="")
        resp, _ = self.audio(chunk)
        self.assertEqual(resp.status_code, 404)
        self.assertIn("áudio tratado", resp.json()["error"])

    def test_older_texts_are_mapped_on_first_play(self):
        from apps.library.models import TextChunk

        for chunk in self.chunks:
            chunk.audio_clip.delete(save=False)
        TextChunk.objects.filter(text=self.text).update(audio_start=None, audio_end=None, audio_clip="")
        resp, _ = self.audio(self.chunks[1])
        self.assertEqual((resp.status_code, resp["Content-Type"]), (200, "audio/mpeg"))
        self.assertTrue(TextChunk.objects.get(pk=self.chunks[1].pk).audio_clip)
