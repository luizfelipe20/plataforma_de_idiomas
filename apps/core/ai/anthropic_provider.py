"""Provedor de IA baseado no Claude (Anthropic Messages API)."""
import json
import logging

import anthropic
from django.conf import settings

from .base import (
    AIProvider,
    AIServiceError,
    GeneratedPracticeText,
    GeneratedQuestion,
    GeneratedQuiz,
    GeneratedTexts,
    SkippedKind,
)

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

TRANSLATION_SYSTEM = (
    "You are a professional translator helping a language learner. Translate each "
    "segment faithfully, preserving meaning, tone and context. The segments are "
    "consecutive parts of one text, so keep terminology consistent across them. "
    "Return exactly one translation per segment, in the same order, without notes."
)

QUIZ_SYSTEM = (
    "You write multiple-choice exercises for language learners based on a text they are studying. "
    "Each question has exactly one clearly correct option; distractors are plausible and related to "
    "what is being tested, so they cannot be eliminated without knowing the language, but they are "
    "unambiguously wrong. No duplicate options, no options that could both be right, no 'all/none of "
    "the above'. Prefer the vocabulary, expressions and structures of the text. Never invent facts "
    "about the text: if the text does not support a requested question type, report it in `skipped` "
    "instead of making things up."
)

TERMS_SYSTEM = (
    "You help a language learner with words and expressions they found hard to understand. "
    "For each term, give a short, clear explanation of what it means in the given context "
    "(one or two sentences), mentioning the usual translation when there is one. No notes outside the JSON."
)

PRACTICE_SYSTEM = (
    "You write short, natural texts that help a language learner memorize specific words and expressions. "
    "Every requested term must appear verbatim (same spelling and form) at least once, used in a context "
    "that makes its meaning clear. Keep the rest of the text simple and coherent so the terms stand out. "
    "Do not add a glossary, notes or markup: return only the title and the plain text."
)

TEXTS_SYSTEM = (
    "You write original reading texts for language learners, following the learner's request. "
    "Texts are natural, coherent and suited to the requested level; when several texts are requested, "
    "each one is distinct in content and angle. Never present invented facts about real people or events "
    "as true. Do not add glossaries, notes, questions or markup: return only titles and plain text."
)


def _texts_schema():
    return {
        "type": "object",
        "properties": {
            "texts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["title", "content"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["texts"],
        "additionalProperties": False,
    }


def _terms_schema():
    return {
        "type": "object",
        "properties": {
            "explanations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["explanations"],
        "additionalProperties": False,
    }


def _practice_schema():
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["title", "content"],
        "additionalProperties": False,
    }


def _translation_schema():
    return {
        "type": "object",
        "properties": {
            "translations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["translations"],
        "additionalProperties": False,
    }


def _quiz_schema(kinds):
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": list(kinds)},
                        "question": {"type": "string"},
                        "passage": {"type": "string"},
                        "options": {"type": "array", "items": {"type": "string"}},
                        "correct_index": {"type": "integer"},
                        "explanation": {"type": "string"},
                    },
                    "required": ["kind", "question", "passage", "options", "correct_index", "explanation"],
                    "additionalProperties": False,
                },
            },
            "skipped": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": list(kinds)},
                        "reason": {"type": "string"},
                    },
                    "required": ["kind", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["questions", "skipped"],
        "additionalProperties": False,
    }


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, client=None):
        self.model = settings.AI_MODEL
        # Credenciais vêm do ambiente (ANTHROPIC_API_KEY ou perfil do `ant auth login`).
        self.client = client or anthropic.Anthropic(
            timeout=settings.AI_TIMEOUT_SECONDS,
            max_retries=settings.AI_MAX_RETRIES,
        )

    # ------------------------------------------------------------------
    def _request_json(self, *, system, prompt, schema, effort, max_tokens=64000):
        # Streaming: respostas longas (quizzes com muitas perguntas) não esbarram no
        # limite de tempo das requisições comuns.
        try:
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                thinking={"type": "adaptive"},
                output_config={
                    "effort": effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                response = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise AIServiceError("Credenciais da IA inválidas. Verifique ANTHROPIC_API_KEY.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIServiceError("A chave de API não tem permissão para este modelo.") from exc
        except anthropic.NotFoundError as exc:
            raise AIServiceError(f"Modelo de IA não encontrado: {self.model}.") from exc
        except anthropic.BadRequestError as exc:
            raise AIServiceError(f"Requisição rejeitada pelo serviço de IA: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise AIServiceError(
                "Limite de requisições da IA atingido. Tente novamente em instantes.", retryable=True
            ) from exc
        except anthropic.APIStatusError as exc:
            raise AIServiceError(
                f"O serviço de IA respondeu com erro ({exc.status_code}). Tente novamente.",
                retryable=exc.status_code >= 500,
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise AIServiceError(
                "Não foi possível conectar ao serviço de IA. Verifique a conexão.", retryable=True
            ) from exc

        if response.stop_reason == "refusal":
            raise AIServiceError("O serviço de IA recusou processar este conteúdo.")
        if response.stop_reason == "max_tokens":
            raise AIServiceError("A resposta da IA foi interrompida (texto muito longo).", retryable=True)

        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise AIServiceError("A IA não retornou conteúdo.", retryable=True)
        try:
            return json.loads(text), response.model
        except json.JSONDecodeError as exc:
            raise AIServiceError("A IA retornou um JSON inválido.", retryable=True) from exc

    # ------------------------------------------------------------------
    def translate_segments(self, segments, source_language, target_language):
        numbered = "\n".join(
            f"<segment index=\"{i}\">\n{seg}\n</segment>" for i, seg in enumerate(segments)
        )
        prompt = (
            f"Translate the following {len(segments)} segments from "
            f"{source_language.english_name} to {target_language.english_name}.\n\n"
            f"{numbered}\n\n"
            f"Return a JSON object whose `translations` array has exactly {len(segments)} items."
        )
        data, _ = self._request_json(
            system=TRANSLATION_SYSTEM,
            prompt=prompt,
            schema=_translation_schema(),
            effort=settings.AI_TRANSLATION_EFFORT,
        )
        translations = data.get("translations") or []
        if len(translations) != len(segments):
            raise AIServiceError(
                f"A IA retornou {len(translations)} traduções para {len(segments)} trechos.",
                retryable=True,
            )
        return [str(t).strip() for t in translations]

    def generate_quiz(self, request):
        plan_lines = "\n".join(
            f"- {item['count']} × kind \"{item['kind']}\" ({item['label']}): {item['instructions']}"
            for item in request.plan
        )
        total = sum(item["count"] for item in request.plan)
        avoid_block = ""
        if request.avoid_questions:
            listed = "\n".join(f"- {q}" for q in request.avoid_questions)
            avoid_block = (
                "\n\nThese questions/answers were already used in earlier quizzes for this same text. Do NOT "
                "repeat them or test the same detail again; explore other parts and aspects of the text:\n"
                f"{listed}"
            )
        prompt = (
            f"<text language=\"{request.source_language.english_name}\">\n{request.text}\n</text>\n\n"
            f"Write {total} multiple-choice questions about this text, exactly as distributed below:\n"
            f"{plan_lines}\n\n"
            f"Rules for every question:\n"
            f"- exactly {request.num_options} options and exactly one correct answer; `correct_index` is the "
            f"0-based index of the correct option;\n"
            f"- write the question statement in {request.quiz_language.english_name}, clear and suited to the "
            f"learner's level; options that test the studied language (words, sentences) stay in "
            f"{request.source_language.english_name};\n"
            f"- `explanation`: briefly justify the correct answer in {request.explanation_language.english_name};\n"
            f"- `passage`: only for kind \"listening\" (verbatim excerpt of the text); empty string otherwise;\n"
            f"- cover different parts of the text instead of testing the same information twice.\n"
            f"If the text does not contain enough material for some kind, generate as many as the text "
            f"honestly supports and add an entry to `skipped` with that kind and the reason (in "
            f"{request.explanation_language.english_name}). (Variation #{request.variation_seed}.)"
            f"{avoid_block}"
        )
        data, model = self._request_json(
            system=QUIZ_SYSTEM,
            prompt=prompt,
            schema=_quiz_schema([item["kind"] for item in request.plan]),
            effort=settings.AI_QUIZ_EFFORT,
        )
        questions = [
            GeneratedQuestion(
                prompt=str(q.get("question", "")).strip(),
                options=[str(o).strip() for o in q.get("options", [])],
                correct_index=q.get("correct_index", -1),
                explanation=str(q.get("explanation", "")).strip(),
                kind=str(q.get("kind", "")),
                passage=str(q.get("passage", "")).strip(),
            )
            for q in data.get("questions", [])
        ]
        skipped = [SkippedKind(str(s.get("kind", "")), str(s.get("reason", "")).strip()) for s in data.get("skipped", [])]
        return GeneratedQuiz(questions=questions, skipped=skipped, model=model)

    def explain_terms(self, terms, source_language, target_language):
        listed = "\n".join(
            f"<term index=\"{i}\">\n<text>{term}</text>\n<context>{context}</context>\n</term>"
            for i, (term, context) in enumerate(terms)
        )
        prompt = (
            f"The learner is studying {source_language.english_name} and marked these {len(terms)} terms as hard "
            f"to understand. Explain each one in {target_language.english_name}.\n\n{listed}\n\n"
            f"Return a JSON object whose `explanations` array has exactly {len(terms)} items, in the same order."
        )
        data, _ = self._request_json(
            system=TERMS_SYSTEM,
            prompt=prompt,
            schema=_terms_schema(),
            effort=settings.AI_TRANSLATION_EFFORT,
        )
        explanations = data.get("explanations") or []
        if len(explanations) != len(terms):
            raise AIServiceError(
                f"A IA retornou {len(explanations)} explicações para {len(terms)} termos.", retryable=True
            )
        return [str(e).strip() for e in explanations]

    def generate_practice_text(self, request):
        listed = "\n".join(
            f"- {term}" + (f" (meaning: {meaning})" if meaning else "") for term, meaning in request.terms
        )
        topic = f"Topic: {request.topic}\n" if request.topic else ""
        prompt = (
            f"Write a new text in {request.source_language.english_name} of about {request.words} words that "
            f"uses ALL of these terms:\n{listed}\n\n{topic}"
            f"The learner's native language is {request.target_language.english_name}; write the title in "
            f"{request.source_language.english_name}. Use paragraphs separated by blank lines. "
            f"(Variation #{request.variation_seed}.)"
        )
        data, model = self._request_json(
            system=PRACTICE_SYSTEM,
            prompt=prompt,
            schema=_practice_schema(),
            effort=settings.AI_QUIZ_EFFORT,
        )
        content = str(data.get("content", "")).strip()
        if not content:
            raise AIServiceError("A IA não retornou o texto de prática.", retryable=True)
        return GeneratedPracticeText(title=str(data.get("title", "")).strip(), content=content, model=model)

    def generate_texts(self, request):
        level = f"Learner level: {request.level}.\n" if request.level else ""
        prompt = (
            f"<request>\n{request.prompt}\n</request>\n\n"
            f"Write {request.count} text{'s' if request.count != 1 else ''} in {request.source_language.english_name} "
            f"following the request above, each of about {request.words} words, with a short title in "
            f"{request.source_language.english_name}.\n{level}"
            f"The learner's native language is {request.target_language.english_name} (the texts are translated "
            f"later; do not translate them yourself). Use paragraphs separated by blank lines. "
            f"Return a JSON object whose `texts` array has exactly {request.count} items. "
            f"(Variation #{request.variation_seed}.)"
        )
        data, model = self._request_json(
            system=TEXTS_SYSTEM,
            prompt=prompt,
            schema=_texts_schema(),
            effort=settings.AI_QUIZ_EFFORT,
        )
        texts = [
            GeneratedPracticeText(title=str(t.get("title", "")).strip(), content=str(t.get("content", "")).strip())
            for t in data.get("texts", [])
        ]
        texts = [t for t in texts if t.content][: request.count]
        if not texts:
            raise AIServiceError("A IA não retornou nenhum texto.", retryable=True)
        return GeneratedTexts(texts=texts, model=model)
