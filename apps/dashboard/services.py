"""
Cálculo dos indicadores do dashboard.

Todos os números vêm do histórico de tentativas **concluídas**: tentativas em
andamento ou abandonadas e quizzes apenas gerados não entram nas médias.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from apps.library.models import Tag, Text
from apps.quizzes.models import Quiz, QuizAttempt

PERIOD_PRESETS = {
    "7": ("Últimos 7 dias", 7),
    "30": ("Últimos 30 dias", 30),
    "90": ("Últimos 90 dias", 90),
    "365": ("Último ano", 365),
    "all": ("Todo o período", None),
    "custom": ("Personalizado", None),
}
RANKING_SIZE = 8


@dataclass
class DashboardFilters:
    period: str = "all"
    date_from: date | None = None
    date_to: date | None = None
    language: str = ""
    tag_ids: list = field(default_factory=list)
    text_id: int | None = None

    @property
    def start(self):
        if self.date_from is None:
            return None
        return timezone.make_aware(datetime.combine(self.date_from, time.min))

    @property
    def end(self):
        if self.date_to is None:
            return None
        return timezone.make_aware(datetime.combine(self.date_to + timedelta(days=1), time.min))

    @property
    def is_filtered(self):
        return bool(self.date_from or self.date_to or self.language or self.tag_ids or self.text_id)


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def parse_filters(params):
    period = params.get("periodo") or "all"
    if period not in PERIOD_PRESETS:
        period = "all"
    filters = DashboardFilters(period=period)
    if period == "custom":
        filters.date_from = _parse_date(params.get("de"))
        filters.date_to = _parse_date(params.get("ate"))
        if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
            filters.date_from, filters.date_to = filters.date_to, filters.date_from
    elif PERIOD_PRESETS[period][1]:
        today = timezone.localdate()
        filters.date_from = today - timedelta(days=PERIOD_PRESETS[period][1] - 1)
        filters.date_to = today
    filters.language = params.get("lang") or ""
    filters.tag_ids = [int(t) for t in params.getlist("tag") if t.isdigit()]
    text_id = params.get("texto")
    filters.text_id = int(text_id) if text_id and text_id.isdigit() else None
    return filters


def filtered_texts(user, filters):
    qs = Text.objects.filter(owner=user)
    if filters.language:
        qs = qs.filter(source_language=filters.language)
    if filters.tag_ids:
        qs = qs.filter(tags__id__in=filters.tag_ids).distinct()
    if filters.text_id:
        qs = qs.filter(pk=filters.text_id)
    return qs


def completed_attempts(user, filters, texts=None):
    texts = texts if texts is not None else filtered_texts(user, filters)
    qs = QuizAttempt.objects.filter(
        user=user, status=QuizAttempt.Status.COMPLETED, text__in=texts.values("pk")
    )
    if filters.start:
        qs = qs.filter(completed_at__gte=filters.start)
    if filters.end:
        qs = qs.filter(completed_at__lt=filters.end)
    return qs


def _mean(values):
    return sum(values) / len(values) if values else None


def adjusted_score(avg, n, global_avg, prior_weight=None):
    """
    Média "suavizada": puxa a média de textos com poucas tentativas em direção à
    média geral. Assim um texto com uma única tentativa ruim não é tratado como o
    mais difícil sem contexto (e uma única tentativa boa não vira o melhor).
    """
    k = settings.DASHBOARD_PRIOR_WEIGHT if prior_weight is None else prior_weight
    if global_avg is None:
        return avg
    return (n * avg + k * global_avg) / (n + k)


def per_text_rows(attempt_rows, texts_by_id, global_avg):
    grouped = defaultdict(list)
    for row in attempt_rows:
        grouped[row["text_id"]].append(row)

    threshold = settings.DASHBOARD_REVIEW_THRESHOLD
    min_attempts = settings.DASHBOARD_MIN_ATTEMPTS
    rows = []
    for text_id, items in grouped.items():
        text = texts_by_id.get(text_id)
        if text is None:
            continue
        items.sort(key=lambda r: r["completed_at"])
        scores = [r["score"] for r in items]
        n = len(items)
        avg = _mean(scores)
        last = items[-1]
        rows.append(
            {
                "text": text,
                "attempts": n,
                "avg_score": avg,
                "adjusted_score": adjusted_score(avg, n, global_avg),
                "avg_correct": _mean([r["correct_count"] for r in items]),
                "avg_wrong": _mean([r["wrong_count"] for r in items]),
                "best_score": max(scores),
                "worst_score": min(scores),
                "last_score": last["score"],
                "last_attempt_at": last["completed_at"],
                "low_confidence": n < min_attempts,
                "needs_review": avg < threshold or last["score"] < threshold,
            }
        )
    return rows


def split_rankings(rows):
    """
    Separa os textos em "mais acertos" (média >= limiar de revisão) e "mais erros".

    - Ordenação pela média ajustada (veja `adjusted_score`).
    - Na lista de dificuldades, textos com poucas tentativas vêm depois dos que
      têm tentativas suficientes: um resultado isolado não é tratado como o
      texto mais difícil sem contexto.
    - Um texto nunca aparece nas duas listas.
    """
    threshold = settings.DASHBOARD_REVIEW_THRESHOLD
    good = [r for r in rows if r["avg_score"] >= threshold]
    weak = [r for r in rows if r["avg_score"] < threshold]
    best = sorted(good, key=lambda r: (r["low_confidence"], -r["adjusted_score"], -r["attempts"], r["text"].title.lower()))
    worst = sorted(weak, key=lambda r: (r["low_confidence"], r["adjusted_score"], -r["attempts"], r["text"].title.lower()))
    return best[:RANKING_SIZE], worst[:RANKING_SIZE]


def tag_performance(attempt_rows, text_tags, tags):
    """
    Desempenho por tag. Um texto com várias tags contribui para cada uma delas;
    por isso os totais por tag **não** devem ser somados entre si.
    """
    buckets = {tag.pk: {"tag": tag, "scores": [], "texts": set(), "wrong": []} for tag in tags}
    untagged = {"tag": None, "scores": [], "texts": set(), "wrong": []}
    for row in attempt_rows:
        tag_ids = text_tags.get(row["text_id"], [])
        targets = [buckets[t] for t in tag_ids if t in buckets] or ([untagged] if not tag_ids else [])
        for bucket in targets:
            bucket["scores"].append(row["score"])
            bucket["wrong"].append(row["wrong_count"])
            bucket["texts"].add(row["text_id"])
    result = []
    for bucket in list(buckets.values()) + [untagged]:
        if not bucket["scores"]:
            continue
        result.append(
            {
                "tag": bucket["tag"],
                "attempts": len(bucket["scores"]),
                "texts": len(bucket["texts"]),
                "avg_score": _mean(bucket["scores"]),
                "avg_wrong": _mean(bucket["wrong"]),
            }
        )
    result.sort(key=lambda r: r["avg_score"])
    return result


def score_evolution(attempt_rows):
    """Média diária de aproveitamento e quantidade de tentativas por dia."""
    per_day = defaultdict(list)
    for row in attempt_rows:
        per_day[timezone.localdate(row["completed_at"])].append(row["score"])
    days = sorted(per_day)
    return {
        "labels": [d.strftime("%d/%m") for d in days],
        "avg_score": [round(_mean(per_day[d]), 1) for d in days],
        "attempts": [len(per_day[d]) for d in days],
    }


def build_dashboard(user, filters):
    texts = filtered_texts(user, filters)
    attempts = completed_attempts(user, filters, texts)
    attempt_rows = list(
        attempts.values("id", "text_id", "quiz_id", "score", "correct_count", "wrong_count", "completed_at")
    )
    texts_by_id = {t.pk: t for t in texts.prefetch_related("tags")}
    text_tags = {t.pk: [tag.pk for tag in t.tags.all()] for t in texts_by_id.values()}

    quizzes = Quiz.objects.filter(text__in=texts.values("pk"))
    if filters.start:
        quizzes = quizzes.filter(created_at__gte=filters.start)
    if filters.end:
        quizzes = quizzes.filter(created_at__lt=filters.end)

    global_avg = _mean([r["score"] for r in attempt_rows])
    rows = per_text_rows(attempt_rows, texts_by_id, global_avg)
    best, worst = split_rankings(rows)
    review = sorted([r for r in rows if r["needs_review"]], key=lambda r: r["adjusted_score"])

    tags = Tag.objects.filter(owner=user)
    if filters.tag_ids:
        tags = tags.filter(pk__in=filters.tag_ids)

    kpis = {
        "total_texts": len(texts_by_id),
        "total_quizzes": quizzes.count(),
        "total_attempts": len(attempt_rows),
        "avg_correct": _mean([r["correct_count"] for r in attempt_rows]),
        "avg_wrong": _mean([r["wrong_count"] for r in attempt_rows]),
        "avg_score": global_avg,
        "texts_studied": len(rows),
        "texts_review": len(review),
    }
    return {
        "kpis": kpis,
        "best": best,
        "worst": worst,
        "review": review[:RANKING_SIZE],
        "tags": tag_performance(attempt_rows, text_tags, tags),
        "evolution": score_evolution(attempt_rows),
        "multi_tag_texts": sum(1 for t in text_tags.values() if len(t) > 1),
        "recent_attempts": attempts.select_related("quiz", "text").order_by("-completed_at")[:8],
    }


def text_report(user, text, filters=None):
    filters = filters or DashboardFilters()
    attempts = completed_attempts(user, filters, Text.objects.filter(pk=text.pk)).select_related("quiz")
    attempts = list(attempts.order_by("completed_at"))
    scores = [a.score for a in attempts]

    quizzes = []
    for quiz in text.quizzes.order_by("version"):
        quiz_scores = [a.score for a in attempts if a.quiz_id == quiz.pk]
        quizzes.append(
            {
                "quiz": quiz,
                "attempts": len(quiz_scores),
                "avg_score": _mean(quiz_scores),
                "best_score": max(quiz_scores) if quiz_scores else None,
                "last_score": quiz_scores[-1] if quiz_scores else None,
            }
        )

    last_study = text.study_sessions.filter(user=user).order_by("-last_beat_at").values_list("last_beat_at", flat=True).first()
    activity_dates = [d for d in [attempts[-1].completed_at if attempts else None, last_study] if d]
    return {
        "attempts": attempts,
        "quizzes": quizzes,
        "summary": {
            "attempts": len(attempts),
            "avg_score": _mean(scores),
            "avg_correct": _mean([a.correct_count for a in attempts]),
            "avg_wrong": _mean([a.wrong_count for a in attempts]),
            "best": max(attempts, key=lambda a: (a.score, a.completed_at)) if attempts else None,
            "worst": min(attempts, key=lambda a: (a.score, -a.completed_at.timestamp())) if attempts else None,
            "last_activity": max(activity_dates) if activity_dates else None,
        },
        "evolution": {
            "labels": [timezone.localtime(a.completed_at).strftime("%d/%m %H:%M") for a in attempts],
            "scores": scores,
            "tooltip_extra": [a.quiz.label for a in attempts],
        },
        "comparison": {
            "labels": [q["quiz"].label for q in quizzes],
            "avg_score": [round(q["avg_score"], 1) if q["avg_score"] is not None else 0 for q in quizzes],
            "attempts": [q["attempts"] for q in quizzes],
        },
    }
