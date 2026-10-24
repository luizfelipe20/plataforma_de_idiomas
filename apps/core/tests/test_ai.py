import json
from types import SimpleNamespace

import anthropic
import httpx2
from django.test import SimpleTestCase

from apps.core.ai import AIServiceError, PracticeTextRequest, QuizRequest
from apps.core.ai.anthropic_provider import AnthropicProvider
from apps.core.ai.mock_provider import MockProvider
from apps.core.languages import get_language


class FakeStream:
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        if self.owner.error:
            raise self.owner.error
        return self.owner.response


class FakeMessages:
    """Imita client.beta.messages.stream(...) do SDK."""

    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self)


def fake_client(payload=None, *, stop_reason="end_turn", error=None):
    content = [SimpleNamespace(type="text", text=json.dumps(payload))] if payload is not None else []
    response = SimpleNamespace(stop_reason=stop_reason, content=content, model="claude-opus-5-5")
    messages = FakeMessages(response, error)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def api_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("erro", response=httpx2.Response(status, request=request), body=None)


EN, PT = get_language("en"), get_language("pt")


class AnthropicProviderTests(SimpleTestCase):
    def test_translation_request_and_parsing(self):
        client, messages = fake_client({"translations": ["Olá.", "Tchau."]})
        result = AnthropicProvider(client=client).translate_segments(["Hi.", "Bye."], EN, PT)
        self.assertEqual(result, ["Olá.", "Tchau."])
        call = messages.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["thinking"], {"type": "adaptive"})
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(call["fallbacks"], "default")
        self.assertIn("Brazilian Portuguese", call["messages"][0]["content"])

    def test_translation_count_mismatch(self):
        client, _ = fake_client({"translations": ["só uma"]})
        with self.assertRaises(AIServiceError):
            AnthropicProvider(client=client).translate_segments(["a", "b"], EN, PT)

    def quiz_request(self, **overrides):
        plan = (
            {"kind": "listening", "count": 2, "label": "Compreensão auditiva", "instructions": "LISTEN-RULES"},
            {"kind": "word_order", "count": 1, "label": "Ordem", "instructions": "ORDER-RULES"},
        )
        values = dict(text="Texto", source_language=EN, quiz_language=PT, explanation_language=PT, plan=plan,
                      num_options=5, avoid_questions=("Pergunta antiga? | resposta antiga",), variation_seed=1)
        values.update(overrides)
        return QuizRequest(**values)

    def test_quiz_request_includes_kinds_previous_questions_and_languages(self):
        payload = {
            "questions": [{"kind": "listening", "question": "Q?", "passage": "Texto", "options": list("abcde"),
                           "correct_index": 2, "explanation": "e"}],
            "skipped": [{"kind": "word_order", "reason": "Frases curtas demais."}],
        }
        client, messages = fake_client(payload)
        quiz = AnthropicProvider(client=client).generate_quiz(self.quiz_request())
        self.assertEqual((quiz.questions[0].kind, quiz.questions[0].passage, quiz.questions[0].correct_index),
                         ("listening", "Texto", 2))
        self.assertEqual(quiz.skipped[0].kind, "word_order")
        call = messages.calls[0]
        prompt = call["messages"][0]["content"]
        for expected in ("Pergunta antiga?", "Brazilian Portuguese", "LISTEN-RULES", "ORDER-RULES",
                         'exactly 5 options', '2 × kind "listening"'):
            self.assertIn(expected, prompt)
        schema = call["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["questions"]["items"]["properties"]["kind"]["enum"], ["listening", "word_order"])
        self.assertIn("skipped", schema["required"])
        self.assertGreater(call["max_tokens"], 16000)  # streaming permite respostas longas

    def test_refusal_and_truncation(self):
        for stop_reason in ("refusal", "max_tokens"):
            client, _ = fake_client({"translations": ["x"]}, stop_reason=stop_reason)
            with self.assertRaises(AIServiceError):
                AnthropicProvider(client=client).translate_segments(["a"], EN, PT)

    def test_errors_are_mapped(self):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        cases = [
            (api_error(anthropic.AuthenticationError, 401), False),
            (api_error(anthropic.RateLimitError, 429), True),
            (api_error(anthropic.InternalServerError, 500), True),
            (anthropic.APIConnectionError(request=request), True),
        ]
        for error, retryable in cases:
            client, _ = fake_client(error=error)
            with self.assertRaises(AIServiceError) as ctx:
                AnthropicProvider(client=client).translate_segments(["a"], EN, PT)
            self.assertEqual(ctx.exception.retryable, retryable, type(error).__name__)

    def test_invalid_json(self):
        response = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="{oops")], model="m")
        client = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(response)))  # noqa: E501
        with self.assertRaises(AIServiceError):
            AnthropicProvider(client=client).translate_segments(["a"], EN, PT)


class AnthropicVocabularyTests(SimpleTestCase):
    def test_explain_terms_sends_context_and_parses(self):
        client, messages = fake_client({"explanations": ["bonde", "consultas"]})
        result = AnthropicProvider(client=client).explain_terms(
            [("tram", "She takes the tram."), ("appointments", "")], EN, PT
        )
        self.assertEqual(result, ["bonde", "consultas"])
        prompt = messages.calls[0]["messages"][0]["content"]
        self.assertIn("<context>She takes the tram.</context>", prompt)
        self.assertIn("Brazilian Portuguese", prompt)

    def test_explain_terms_count_mismatch(self):
        client, _ = fake_client({"explanations": ["bonde"]})
        with self.assertRaises(AIServiceError):
            AnthropicProvider(client=client).explain_terms([("tram", ""), ("app", "")], EN, PT)

    def test_practice_text_request_and_parsing(self):
        client, messages = fake_client({"title": "A trip", "content": "I took the tram."})
        request = PracticeTextRequest(terms=(("tram", "bonde"),), source_language=EN, target_language=PT, words=120, topic="travel")
        result = AnthropicProvider(client=client).generate_practice_text(request)
        self.assertEqual((result.title, result.content, result.model), ("A trip", "I took the tram.", "claude-opus-5-5"))
        prompt = messages.calls[0]["messages"][0]["content"]
        for expected in ("- tram (meaning: bonde)", "about 120 words", "Topic: travel"):
            self.assertIn(expected, prompt)

    def test_practice_text_empty_content(self):
        client, _ = fake_client({"title": "x", "content": "  "})
        request = PracticeTextRequest(terms=(("tram", ""),), source_language=EN, target_language=PT, words=120)
        with self.assertRaises(AIServiceError):
            AnthropicProvider(client=client).generate_practice_text(request)


class MockProviderTests(SimpleTestCase):
    def test_mock_generates_every_kind_with_five_options(self):
        from apps.quizzes.kinds import KINDS

        text = ("Ana is happy because she lives in a small city. She works at a school near the river. "
                "Every morning she takes the old tram to work. She prefers quiet places and clear goals. "
                "On Sundays she visits her family and they cook a big lunch together.")
        plan = tuple({"kind": k, "count": 1, "label": KINDS[k].label, "instructions": ""} for k in KINDS)
        quiz = MockProvider().generate_quiz(QuizRequest(
            text=text, source_language=EN, quiz_language=PT, explanation_language=PT, plan=plan, num_options=5,
        ))
        self.assertEqual(sorted(q.kind for q in quiz.questions), sorted(KINDS))
        self.assertEqual(quiz.skipped, [])
        for q in quiz.questions:
            self.assertEqual(len(q.options), 5, q.kind)
            self.assertEqual(len({o.lower() for o in q.options}), 5, q.kind)
            self.assertIn(q.correct_index, range(5))

    def test_mock_reports_kind_without_material(self):
        quiz = MockProvider().generate_quiz(QuizRequest(
            text="Bob sells cars.", source_language=EN, quiz_language=PT, explanation_language=PT,
            plan=({"kind": "synonym_antonym", "count": 2, "label": "", "instructions": ""},), num_options=5,
        ))
        self.assertEqual(quiz.questions, [])
        self.assertEqual(quiz.skipped[0].kind, "synonym_antonym")
