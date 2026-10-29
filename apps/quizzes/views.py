from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Max, Min, Q
from django.conf import settings
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.core.ai import AIServiceError
from apps.core.http import ranged_file_response
from apps.core.languages import SUPPORTED_LANGUAGES
from apps.library.models import Text
from apps.library.services import tts
from apps.library.views import player_context

from . import services
from .forms import QuizBuilderForm
from .models import Question, Quiz, QuizAttempt, StudyMode


def _user_quiz(request, pk):
    return get_object_or_404(Quiz.objects.select_related("text"), pk=pk, text__owner=request.user)


def _user_attempt(request, pk):
    return get_object_or_404(
        QuizAttempt.objects.select_related("quiz", "text"), pk=pk, user=request.user, text__owner=request.user
    )


def _after_generation(request, quiz, *, start_mode=None):
    total = quiz.questions.count()
    note = f" Observação: {quiz.generation_notes}" if quiz.generation_notes else ""
    messages.success(request, f"{quiz.label} criado com {total} pergunta{'s' if total != 1 else ''}.{note}")
    if start_mode is not None:
        mode = start_mode if start_mode in StudyMode.values else StudyMode.READING
        return redirect(services.start_attempt(quiz, request.user, mode))
    return redirect(quiz)


@login_required
@require_POST
def quiz_generate(request, text_pk):
    """Geração rápida com a combinação padrão de tipos (QUIZ_DEFAULT_PLAN)."""
    text = get_object_or_404(Text, pk=text_pk, owner=request.user)
    language = request.POST.get("language") or text.effective_quiz_language
    if language not in SUPPORTED_LANGUAGES:
        messages.error(request, "Idioma do quiz inválido.")
        return redirect(text)
    try:
        quiz = services.generate_quiz(text, language=language)
    except (AIServiceError, services.QuizValidationError) as exc:
        messages.error(request, f"Não foi possível gerar o quiz: {exc}")
        return redirect(f"{text.get_absolute_url()}#quizzes")
    if request.POST.get("next") == "start":
        return _after_generation(request, quiz, start_mode=request.POST.get("mode") or StudyMode.READING)
    _after_generation(request, quiz)
    return redirect(f"{text.get_absolute_url()}#quizzes")


@login_required
def quiz_new(request, text_pk):
    """Criação de quiz escolhendo os tipos de pergunta e a quantidade de cada um."""
    text = get_object_or_404(Text, pk=text_pk, owner=request.user)
    form = QuizBuilderForm(request.POST or None, text=text)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            quiz = services.generate_quiz(text, plan=data["plan"], language=data["language"], title=data["title"])
        except (AIServiceError, services.QuizValidationError) as exc:
            form.add_error(None, f"Não foi possível gerar o quiz: {exc}")
        else:
            start = "start" in request.POST
            return _after_generation(request, quiz, start_mode=(data.get("mode") or StudyMode.READING) if start else None)
    return render(
        request,
        "quizzes/quiz_new.html",
        {
            "text": text,
            "form": form,
            "max_questions": settings.QUIZ_MAX_QUESTIONS,
            "max_per_kind": settings.QUIZ_MAX_PER_KIND,
            "options_per_question": settings.QUIZ_OPTIONS_PER_QUESTION,
            "previous_quizzes": text.quizzes.count(),
        },
    )


@login_required
@require_GET
def question_audio(request, pk):
    """MP3 do trecho de uma pergunta de compreensão auditiva (somente para o dono do texto)."""
    question = get_object_or_404(
        Question.objects.select_related("quiz__text__source_media"), pk=pk, quiz__text__owner=request.user
    )
    if question.audio_clip or question.has_real_audio:
        from apps.videos.services import question_clip

        path = question_clip(question)
        if path:
            return ranged_file_response(request, path, content_type="audio/mpeg", max_age=86400)
        # Trecho do vídeo sem áudio tratado: não troca em silêncio pelo original nem por TTS.
        return JsonResponse({"error": "O áudio tratado deste trecho do vídeo não está disponível."}, status=404)
    try:
        path = tts.get_audio(question.passage, question.quiz.text.source_language)
    except tts.TTSError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    response = FileResponse(open(path, "rb"), content_type="audio/mpeg")
    response["Cache-Control"] = "private, max-age=86400"
    return response


@login_required
def quiz_detail(request, pk):
    quiz = _user_quiz(request, pk)
    attempts = quiz.attempts.filter(user=request.user).order_by("-started_at")
    completed = attempts.filter(status=QuizAttempt.Status.COMPLETED)
    stats = completed.aggregate(
        count=Count("id"), avg=Avg("score"), best=Max("score"), worst=Min("score"),
        avg_correct=Avg("correct_count"), avg_wrong=Avg("wrong_count"),
    )
    return render(
        request,
        "quizzes/quiz_detail.html",
        {
            "quiz": quiz,
            "text": quiz.text,
            "questions": quiz.questions.prefetch_related("options"),
            "attempts": attempts,
            "stats": stats,
            "study_modes": StudyMode.choices,
            "show_answers": request.GET.get("respostas") == "1",
            "kinds_used": quiz.kinds_used,
        },
    )


@login_required
def quiz_delete(request, pk):
    quiz = _user_quiz(request, pk)
    if request.method == "POST":
        text, label = quiz.text, quiz.label
        quiz.delete()  # perguntas, alternativas e tentativas vão junto; o tempo de estudo é mantido
        messages.success(request, f"{label} foi excluído.")
        return redirect(f"{text.get_absolute_url()}#quizzes")
    attempts = quiz.attempts.all()
    return render(
        request,
        "quizzes/quiz_confirm_delete.html",
        {
            "quiz": quiz,
            "text": quiz.text,
            "question_count": quiz.questions.count(),
            "attempt_count": attempts.count(),
            "completed_count": attempts.filter(status=QuizAttempt.Status.COMPLETED).count(),
        },
    )


@login_required
@require_POST
def attempt_start(request, pk):
    quiz = _user_quiz(request, pk)
    mode = request.POST.get("mode")
    if mode not in StudyMode.values:
        mode = StudyMode.READING
    attempt = services.start_attempt(quiz, request.user, mode)
    return redirect(attempt)


@login_required
def attempt_take(request, pk):
    attempt = _user_attempt(request, pk)
    if attempt.status == QuizAttempt.Status.COMPLETED:
        return redirect("quizzes:attempt_result", pk=attempt.pk)
    if attempt.status == QuizAttempt.Status.ABANDONED:
        messages.info(request, "Esta tentativa foi abandonada. Inicie uma nova.")
        return redirect(attempt.quiz)

    questions = list(attempt.quiz.questions.select_related("quiz__text__source_media").prefetch_related("options"))
    chunks = list(attempt.text.chunks.all())
    selections, error = {}, None
    if request.method == "POST":
        for question in questions:
            raw = request.POST.get(f"q{question.pk}", "")
            if raw.isdigit():
                selections[question.pk] = int(raw)
        try:
            services.submit_attempt(attempt, selections)
        except services.AttemptError as exc:
            error = str(exc)
        else:
            return redirect("quizzes:attempt_result", pk=attempt.pk)

    return render(
        request,
        "quizzes/attempt_take.html",
        {
            "attempt": attempt,
            "quiz": attempt.quiz,
            "text": attempt.text,
            "chunks": chunks,
            **player_context(chunks),
            "server_tts": tts.server_tts_enabled(),
            # Com perguntas de compreensão auditiva o texto começa oculto (pode ser exibido).
            "hide_reference": attempt.mode == StudyMode.AUDIO_ONLY or any(q.kind == "listening" for q in questions),
            "questions": questions,
            "selections": selections,
            "error": error,
            "study_modes": StudyMode.choices,
        },
    )


@login_required
def attempt_result(request, pk):
    attempt = _user_attempt(request, pk)
    if not attempt.is_completed:
        return redirect(attempt)
    answers = {a.question_id: a for a in attempt.answers.select_related("selected_option")}
    review = []
    for question in attempt.quiz.questions.prefetch_related("options"):
        answer = answers.get(question.pk)
        review.append({"question": question, "answer": answer, "correct": question.correct_option})
    previous = (
        QuizAttempt.objects.filter(
            quiz=attempt.quiz, user=request.user, status=QuizAttempt.Status.COMPLETED,
            completed_at__lt=attempt.completed_at,
        )
        .order_by("-completed_at")
        .first()
    )
    return render(
        request,
        "quizzes/attempt_result.html",
        {"attempt": attempt, "quiz": attempt.quiz, "text": attempt.text, "review": review, "previous": previous},
    )


@login_required
def attempt_history(request):
    qs = (
        QuizAttempt.objects.filter(user=request.user, text__owner=request.user)
        .select_related("quiz", "text")
        .order_by("-started_at")
    )
    status = request.GET.get("status") or "completed"
    if status in QuizAttempt.Status.values:
        qs = qs.filter(status=status)
    text_id = request.GET.get("texto")
    if text_id and text_id.isdigit():
        qs = qs.filter(text_id=int(text_id))
    quiz_id = request.GET.get("quiz")
    if quiz_id and quiz_id.isdigit():
        qs = qs.filter(quiz_id=int(quiz_id))
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    texts = Text.objects.filter(owner=request.user).annotate(
        n=Count("attempts", filter=Q(attempts__user=request.user))
    ).filter(n__gt=0).order_by("title")
    return render(
        request,
        "quizzes/attempt_history.html",
        {
            "page": page,
            "texts": texts,
            "status": status,
            "statuses": QuizAttempt.Status.choices,
            "text_id": int(text_id) if text_id and text_id.isdigit() else None,
            "quiz_filter": _user_quiz(request, int(quiz_id)) if quiz_id and quiz_id.isdigit() else None,
            "base_url": reverse("quizzes:attempt_history"),
        },
    )
