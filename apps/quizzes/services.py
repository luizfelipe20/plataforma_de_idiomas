"""Regras de negócio de quizzes: geração, validação, tentativas e correção."""
import difflib
import logging
import random
import re
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.utils import timezone

from apps.core.ai import AIServiceError, QuizRequest, get_provider
from apps.core.languages import get_language

from .kinds import FILL_BLANK, KINDS, LISTENING, WORD_ORDER, normalize_plan
from .models import AttemptAnswer, Option, Question, Quiz, QuizAttempt

logger = logging.getLogger(__name__)


class QuizValidationError(ValueError):
    pass


def _normalize(text):
    return re.sub(r"\W+", " ", text.lower()).strip()


def is_similar(a, b, threshold=None):
    threshold = settings.QUIZ_SIMILARITY_THRESHOLD if threshold is None else threshold
    a, b = _normalize(a), _normalize(b)
    if not a or not b:
        return False
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= threshold


def question_signature(prompt, correct_answer, passage=""):
    """Descrição textual de uma pergunta já usada (enviada à IA para evitar repetições)."""
    return " | ".join(part for part in (prompt, passage, correct_answer) if part)


def same_question(a, b):
    """
    `a` e `b` são tuplas (enunciado, resposta correta, trecho).

    Tipos como "Identifique o erro" repetem o enunciado e mudam só as
    alternativas, então uma pergunta só é repetida quando enunciado, resposta
    e trecho de áudio coincidem (com tolerância a pequenas diferenças).
    """
    (prompt_a, answer_a, passage_a), (prompt_b, answer_b, passage_b) = a, b
    if not is_similar(prompt_a, prompt_b) or not is_similar(answer_a, answer_b, threshold=0.9):
        return False
    if passage_a or passage_b:
        return is_similar(passage_a, passage_b)
    return True


def _identity(q):
    return (q.prompt, q.options[q.correct_index], q.passage)


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------
LETTER_PREFIX_RE = re.compile(r"^\s*(?:[A-Ea-e]|[1-5])\s*[).:-]\s+")


def clean_question(q):
    """Correções automáticas seguras: espaços e prefixos de letra ("A) ...") nas alternativas."""
    q.prompt = " ".join(q.prompt.split())
    q.passage = " ".join((q.passage or "").split())
    q.explanation = q.explanation.strip()
    options = [LETTER_PREFIX_RE.sub("", " ".join(str(o).split())) for o in q.options]
    # Só remove prefixos se todas as alternativas os tinham (senão poderia mudar o sentido).
    if all(LETTER_PREFIX_RE.match(" ".join(str(o).split())) for o in q.options):
        q.options = options
    else:
        q.options = [" ".join(str(o).split()) for o in q.options]
    return q


def _fix_listening_passage(q, text):
    """Garante que o trecho em áudio exista no texto; aproxima para a frase real mais parecida."""
    norm_text = _normalize(text)
    if q.passage and _normalize(q.passage) in norm_text:
        return True
    if not q.passage:
        return False
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+", text) if s.strip()]
    best = max(sentences, key=lambda s: difflib.SequenceMatcher(None, _normalize(s), _normalize(q.passage)).ratio(),
               default="")
    if best and difflib.SequenceMatcher(None, _normalize(best), _normalize(q.passage)).ratio() >= 0.85:
        q.passage = best
        return True
    return False


def validation_errors(q, *, num_options, text):
    """Lista de problemas de uma pergunta gerada (vazia = válida)."""
    errors = []
    if q.kind not in KINDS:
        errors.append(f"tipo desconhecido ({q.kind!r})")
    if not q.prompt:
        errors.append("enunciado vazio")
    if len(q.options) != num_options:
        errors.append(f"{len(q.options)} alternativas (esperado {num_options})")
    if any(not o for o in q.options):
        errors.append("alternativa vazia")
    if len({_normalize(o) for o in q.options}) != len(q.options):
        errors.append("alternativas repetidas")
    if not isinstance(q.correct_index, int) or isinstance(q.correct_index, bool) or not 0 <= q.correct_index < len(q.options):
        errors.append("não indica exatamente uma alternativa correta")
    if errors:
        return errors
    if q.kind == FILL_BLANK and "___" not in q.prompt:
        errors.append("frase para completar sem lacuna (_____)")
    if q.kind == LISTENING and not _fix_listening_passage(q, text):
        errors.append("trecho de áudio ausente ou que não existe no texto")
    if q.kind == WORD_ORDER:
        bags = {tuple(sorted(_normalize(o).split())) for o in q.options}
        if len(bags) != 1:
            errors.append("alternativas de ordenação não usam as mesmas palavras")
    if q.kind != LISTENING:
        q.passage = ""
    return errors


def validate_generated(generated, *, num_options=None, text=""):
    """Valida um conjunto completo de perguntas (usado também por testes/ferramentas)."""
    num_options = num_options or settings.QUIZ_OPTIONS_PER_QUESTION
    signatures = []
    for idx, q in enumerate(generated.questions, start=1):
        clean_question(q)
        errors = validation_errors(q, num_options=num_options, text=text)
        if errors:
            raise QuizValidationError(f"Pergunta {idx}: " + "; ".join(errors) + ".")
        if any(same_question(_identity(q), other) for other in signatures):
            raise QuizValidationError(f"Pergunta {idx} repete outra pergunta do mesmo quiz.")
        signatures.append(_identity(q))


# ---------------------------------------------------------------------------
# Geração
# ---------------------------------------------------------------------------
def previous_identities(text):
    """(enunciado, resposta correta, trecho) das perguntas já usadas nos quizzes deste texto."""
    rows = Question.objects.filter(quiz__text=text, options__is_correct=True).values_list(
        "prompt", "options__text", "passage"
    )
    return [tuple(row) for row in rows]


def previous_signatures(text):
    return [question_signature(prompt, answer, passage) for prompt, answer, passage in previous_identities(text)]


def previous_questions(text):
    return list(Question.objects.filter(quiz__text=text).values_list("prompt", flat=True))


def count_repeats(generated, previous):
    """Quantas perguntas de `generated` repetem alguma identidade de `previous`."""
    return sum(1 for q in generated.questions if any(same_question(_identity(q), p) for p in previous))


def validate_plan(plan):
    plan = normalize_plan(plan)
    if not plan:
        raise QuizValidationError("Selecione pelo menos um tipo de pergunta.")
    if any(count > settings.QUIZ_MAX_PER_KIND for count in plan.values()):
        raise QuizValidationError(f"No máximo {settings.QUIZ_MAX_PER_KIND} perguntas por tipo.")
    if sum(plan.values()) > settings.QUIZ_MAX_QUESTIONS:
        raise QuizValidationError(f"No máximo {settings.QUIZ_MAX_QUESTIONS} perguntas por quiz.")
    return plan


def _redistribute(needed, kind, plan, blocked):
    """Passa as perguntas pendentes de `kind` para os outros tipos selecionados (rodízio)."""
    blocked.add(kind)
    moved = needed.pop(kind, 0)
    others = [k for k in plan if k not in blocked]
    targets = [others[i % len(others)] for i in range(moved)] if others else []
    for target in targets:
        needed[target] = needed.get(target, 0) + 1
    return moved, targets


def generate_quiz(text, *, plan=None, language=None, title="", provider=None, max_rounds=None):
    """
    Gera e persiste um novo quiz independente para `text`.

    `plan` = {tipo: quantidade}. Cada pergunta é validada isoladamente: as que
    falham são corrigidas (quando seguro) ou geradas de novo nas rodadas
    seguintes, pedindo à IA só as que faltam. Tipos que o texto não comporta
    são substituídos por outros tipos selecionados, com aviso nas observações.
    Quizzes anteriores e seus resultados nunca são alterados.
    Levanta `AIServiceError` ou `QuizValidationError` em caso de falha.
    """
    provider = provider or get_provider()
    plan = validate_plan(plan if plan is not None else settings.QUIZ_DEFAULT_PLAN)
    language = language or text.effective_quiz_language
    max_rounds = max_rounds or settings.QUIZ_GENERATION_ROUNDS
    num_options = settings.QUIZ_OPTIONS_PER_QUESTION
    previous = previous_identities(text)
    next_version = (text.quizzes.aggregate(v=Max("version"))["v"] or 0) + 1

    needed, blocked = dict(plan), set()
    accepted, accepted_ids, notes, rejected = [], [], [], 0
    model_name = ""
    for round_no in range(max_rounds):
        request_plan = {k: n for k, n in needed.items() if n > 0}
        if not request_plan:
            break
        last_round = round_no == max_rounds - 1
        generated = provider.generate_quiz(
            QuizRequest(
                text=text.content,
                source_language=get_language(text.source_language),
                quiz_language=get_language(language),
                explanation_language=get_language(text.target_language),
                plan=tuple(
                    {"kind": k, "count": n, "label": KINDS[k].label, "instructions": KINDS[k].ai_instructions}
                    for k, n in request_plan.items()
                ),
                num_options=num_options,
                avoid_questions=tuple(question_signature(*row) for row in previous + accepted_ids),
                variation_seed=next_version * 10 + round_no,
            )
        )
        model_name = generated.model or model_name
        for q in generated.questions:
            if needed.get(q.kind, 0) <= 0:
                continue  # tipo não pedido ou já completo
            clean_question(q)
            errors = validation_errors(q, num_options=num_options, text=text.content)
            identity = _identity(q) if not errors else None
            if not errors and any(same_question(identity, other) for other in accepted_ids):
                errors.append("repete outra pergunta do quiz")
            # Repetir quizzes anteriores só é tolerado na última rodada.
            if not errors and not last_round and any(same_question(identity, p) for p in previous):
                errors.append("repete pergunta de quiz anterior")
            if errors:
                rejected += 1
                logger.info("Pergunta descartada (texto %s, %s): %s", text.pk, q.kind, "; ".join(errors))
                continue
            accepted.append(q)
            accepted_ids.append(identity)
            needed[q.kind] -= 1
        for skip in generated.skipped:
            if needed.get(skip.kind, 0) > 0:
                moved, targets = _redistribute(needed, skip.kind, plan, blocked)
                if targets:
                    labels = ", ".join(sorted({KINDS[t].label for t in targets}))
                    notes.append(f"{KINDS[skip.kind].label}: {skip.reason} {moved} pergunta(s) substituída(s) por: {labels}.")
                else:
                    notes.append(f"{KINDS[skip.kind].label}: {skip.reason}")

    if not accepted:
        raise QuizValidationError(
            "Não foi possível gerar perguntas válidas para este texto. " + " ".join(notes)
        )
    requested = sum(plan.values())
    if len(accepted) < requested:
        notes.append(f"Foram geradas {len(accepted)} de {requested} perguntas.")
    repeats = sum(1 for identity in accepted_ids if any(same_question(identity, p) for p in previous))
    if repeats:
        notes.append(f"{repeats} pergunta(s) semelhante(s) a quizzes anteriores.")

    # Agrupa por tipo, na ordem escolhida pelo usuário.
    order = {k: i for i, k in enumerate(plan)}
    accepted.sort(key=lambda q: order.get(q.kind, len(order)))
    final_plan = {}
    for q in accepted:
        final_plan[q.kind] = final_plan.get(q.kind, 0) + 1
    config = {"plan": plan, "requested": requested, "generated": final_plan, "rejected": rejected}

    rng = random.Random()
    for _ in range(3):  # tolera corrida na numeração de versões
        try:
            with transaction.atomic():
                version = (text.quizzes.aggregate(v=Max("version"))["v"] or 0) + 1
                quiz = Quiz.objects.create(
                    text=text, version=version, language=language, title=title.strip()[:120],
                    config=config, options_per_question=num_options,
                    ai_model=model_name[:100], generation_notes=" ".join(notes)[:500],
                )
                for position, gq in enumerate(accepted):
                    question = Question.objects.create(
                        quiz=quiz, order=position, kind=gq.kind, prompt=gq.prompt,
                        passage=gq.passage, explanation=gq.explanation,
                    )
                    # Embaralha para a resposta correta não ficar sempre na mesma posição.
                    options = [(opt, i == gq.correct_index) for i, opt in enumerate(gq.options)]
                    rng.shuffle(options)
                    Option.objects.bulk_create(
                        Option(question=question, order=i, text=opt[:500], is_correct=correct)
                        for i, (opt, correct) in enumerate(options)
                    )
                return quiz
        except IntegrityError:
            continue
    raise AIServiceError("Não foi possível salvar o quiz. Tente novamente.", retryable=True)


# ---------------------------------------------------------------------------
# Tentativas
# ---------------------------------------------------------------------------
def start_attempt(quiz, user, mode):
    return QuizAttempt.objects.create(
        quiz=quiz,
        text=quiz.text,
        user=user,
        mode=mode,
        total_questions=quiz.questions.count(),
    )


class AttemptError(ValueError):
    pass


@transaction.atomic
def submit_attempt(attempt, selections):
    """
    Corrige uma tentativa. `selections` mapeia question_id -> option_id.

    Cada envio cria registros novos; uma tentativa concluída não pode ser
    reenviada, preservando o histórico.
    """
    attempt = QuizAttempt.objects.select_for_update().get(pk=attempt.pk)
    if attempt.status != QuizAttempt.Status.IN_PROGRESS:
        raise AttemptError("Esta tentativa já foi finalizada.")

    questions = list(attempt.quiz.questions.prefetch_related("options"))
    missing = [q.order + 1 for q in questions if q.pk not in selections]
    if missing:
        raise AttemptError("Responda todas as perguntas. Faltando: " + ", ".join(map(str, missing)))

    answers, correct = [], 0
    for question in questions:
        option = next((o for o in question.options.all() if o.pk == selections[question.pk]), None)
        if option is None:
            raise AttemptError(f"Alternativa inválida na pergunta {question.order + 1}.")
        correct += option.is_correct
        answers.append(
            AttemptAnswer(attempt=attempt, question=question, selected_option=option, is_correct=option.is_correct)
        )
    AttemptAnswer.objects.bulk_create(answers)

    total = len(questions)
    attempt.total_questions = total
    attempt.correct_count = correct
    attempt.wrong_count = total - correct
    attempt.score = round(100 * correct / total) if total else 0
    attempt.status = QuizAttempt.Status.COMPLETED
    attempt.completed_at = timezone.now()
    attempt.save()
    return attempt


def mark_abandoned_attempts(now=None):
    now = now or timezone.now()
    cutoff = now - timedelta(hours=settings.QUIZ_ATTEMPT_ABANDON_HOURS)
    return QuizAttempt.objects.filter(
        status=QuizAttempt.Status.IN_PROGRESS, started_at__lt=cutoff
    ).update(status=QuizAttempt.Status.ABANDONED)


def quizzes_with_stats(text, user):
    """Quizzes do texto anotados com tentativas, melhor e última pontuação do usuário."""
    completed = Q(attempts__status=QuizAttempt.Status.COMPLETED, attempts__user=user)
    last_score = (
        QuizAttempt.objects.filter(quiz=OuterRef("pk"), user=user, status=QuizAttempt.Status.COMPLETED)
        .order_by("-completed_at")
        .values("score")[:1]
    )
    question_count = (
        Question.objects.filter(quiz=OuterRef("pk")).order_by().values("quiz").annotate(n=Count("pk")).values("n")
    )
    return (
        text.quizzes.annotate(
            question_count=Subquery(question_count),
            attempt_count=Count("attempts", filter=completed),
            best_score=Max("attempts__score", filter=completed),
            last_attempt_at=Max("attempts__completed_at", filter=completed),
            last_score=Subquery(last_score),
        )
        .order_by("-version")
    )
