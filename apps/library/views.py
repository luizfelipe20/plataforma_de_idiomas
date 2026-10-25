from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.core.ai import AIServiceError
from apps.core.languages import LANGUAGE_CHOICES
from apps.quizzes.models import QuizAttempt, StudyMode
from apps.quizzes.services import quizzes_with_stats
from apps.vocabulary.services import terms_for_language

from .forms import TagForm, TextForm, TextGenerationForm
from .models import Tag, Text, TextChunk
from .services.generation import MAX_TEXTS_PER_REQUEST, generate_texts
from .services import tts
from .services.texts import save_text, translate_text

SORT_OPTIONS = {
    "recent": ("-updated_at", "Atualização (recente)"),
    "oldest_update": ("updated_at", "Atualização (antiga)"),
    "created": ("-created_at", "Criação (recente)"),
    "created_old": ("created_at", "Criação (antiga)"),
    "title": ("title", "Título (A–Z)"),
    "title_desc": ("-title", "Título (Z–A)"),
}


def _user_texts(request):
    return Text.objects.filter(owner=request.user)


def player_context(chunks):
    """Dados do player de áudio: texto de cada trecho e, se habilitado, a URL do MP3."""
    chunks = list(chunks)
    return {
        "chunk_texts": [c.content for c in chunks],
        "chunk_audio_urls": [reverse("library:chunk_audio", args=[c.pk]) for c in chunks]
        if tts.server_tts_enabled()
        else [],
    }


def filter_texts(qs, params):
    """Aplica busca, filtros de tags/idioma e ordenação (combináveis)."""
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(content__icontains=q))
    language = params.get("lang")
    if language:
        qs = qs.filter(source_language=language)
    tag_ids = [int(t) for t in params.getlist("tag") if t.isdigit()]
    if tag_ids:
        if params.get("match") == "all":
            for tag_id in tag_ids:
                qs = qs.filter(tags__id=tag_id)
        else:
            qs = qs.filter(tags__id__in=tag_ids)
        qs = qs.distinct()
    sort = params.get("sort") if params.get("sort") in SORT_OPTIONS else "recent"
    return qs.order_by(SORT_OPTIONS[sort][0]), {"q": q, "lang": language, "tags": tag_ids, "sort": sort,
                                               "match": params.get("match") or "any"}


@login_required
def text_list(request):
    qs, active = filter_texts(_user_texts(request), request.GET)
    qs = qs.prefetch_related("tags", "chunks").annotate(
        quiz_count=Count("quizzes", distinct=True),
        attempt_count=Count("attempts", filter=Q(attempts__status=QuizAttempt.Status.COMPLETED), distinct=True),
    )
    page = Paginator(qs, 12).get_page(request.GET.get("page"))
    tags = Tag.objects.filter(owner=request.user).annotate(text_count=Count("texts"))
    return render(
        request,
        "library/text_list.html",
        {
            "page": page,
            "tags": tags,
            "active": active,
            "sort_options": [(k, v[1]) for k, v in SORT_OPTIONS.items()],
            "language_choices": LANGUAGE_CHOICES,
            "total": Text.objects.filter(owner=request.user).count(),
        },
    )


@login_required
def text_create(request):
    form = TextForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        text = form.save(commit=False)
        text.owner = request.user
        save_text(text, tags=form.cleaned_data["tags"], new_tag_names=form.cleaned_data["new_tags"])
        _report_translation(request, text, created=True)
        return redirect(text)
    return render(request, "library/text_form.html", {"form": form, "chunk_size": settings.TEXT_CHUNK_MAX_CHARS})


@login_required
def text_generate(request):
    form = TextGenerationForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            texts = generate_texts(
                request.user,
                data["prompt"],
                source_language=data["source_language"],
                target_language=data["target_language"],
                count=data["count"],
                length=data["length"],
                level=data["level"],
                tags=data["tags"],
                new_tag_names=data["new_tags"],
            )
        except AIServiceError as exc:
            form.add_error(None, f"Não foi possível gerar os textos: {exc}")
        else:
            failed = [t for t in texts if t.status == Text.Status.ERROR]
            if len(texts) == 1:
                _report_translation(request, texts[0], created=True)
                return redirect(texts[0])
            titles = ", ".join(f"“{t.title}”" for t in texts)
            messages.success(request, f"{len(texts)} textos criados: {titles}.")
            if failed:
                messages.warning(request, f"A tradução falhou em {len(failed)} deles; abra o texto para tentar de novo.")
            return redirect("library:text_list")
    return render(
        request, "library/text_generate.html", {"form": form, "max_texts": MAX_TEXTS_PER_REQUEST}
    )


@login_required
def text_update(request, pk):
    text = get_object_or_404(_user_texts(request), pk=pk)
    form = TextForm(request.POST or None, instance=text, owner=request.user)
    if request.method == "POST" and form.is_valid():
        text = form.save(commit=False)
        save_text(text, tags=form.cleaned_data["tags"], new_tag_names=form.cleaned_data["new_tags"])
        _report_translation(request, text, created=False)
        return redirect(text)
    return render(request, "library/text_form.html", {"form": form, "text": text, "chunk_size": settings.TEXT_CHUNK_MAX_CHARS})


def _report_translation(request, text, created):
    verb = "cadastrado" if created else "atualizado"
    if text.status == Text.Status.ERROR:
        messages.warning(request, f"Texto {verb}, mas a tradução falhou: {text.status_message}")
    else:
        messages.success(request, f"Texto {verb} com sucesso.")


@login_required
def text_delete(request, pk):
    text = get_object_or_404(_user_texts(request), pk=pk)
    if request.method == "POST":
        title = text.title
        text.delete()
        messages.success(request, f"“{title}” foi excluído.")
        return redirect("library:text_list")
    return render(
        request,
        "library/text_confirm_delete.html",
        {"text": text, "quiz_count": text.quizzes.count(), "attempt_count": text.attempts.count()},
    )


@login_required
def text_detail(request, pk):
    text = get_object_or_404(_user_texts(request).prefetch_related("tags", "chunks"), pk=pk)
    return render(
        request,
        "library/text_detail.html",
        {
            "text": text,
            "chunks": text.chunks.all(),
            "quizzes": quizzes_with_stats(text, request.user),
            "language_choices": LANGUAGE_CHOICES,
            "study_modes": StudyMode.choices,
            "difficult_terms": terms_for_language(request.user, text.source_language),
            "practiced_terms": text.practiced_terms.all(),
            **player_context(text.chunks.all()),
        },
    )


@login_required
@require_POST
def text_translate(request, pk):
    text = get_object_or_404(_user_texts(request), pk=pk)
    if translate_text(text, force=request.POST.get("force") == "1"):
        messages.success(request, "Tradução atualizada.")
    else:
        messages.error(request, f"Não foi possível traduzir: {text.status_message}")
    return redirect(text)


@login_required
def study(request, pk):
    text = get_object_or_404(_user_texts(request).prefetch_related("chunks"), pk=pk)
    mode = request.GET.get("modo")
    if mode not in StudyMode.values:
        mode = StudyMode.READING
    activity = {"reading": "reading", "reading_audio": "audio", "audio_only": "listening"}[mode]
    chunks = list(text.chunks.all())
    return render(
        request,
        "library/study.html",
        {
            "text": text,
            "chunks": chunks,
            "chunk_translations": [c.translation for c in chunks],
            **player_context(chunks),
            "mode": mode,
            "study_modes": StudyMode.choices,
            "activity": activity,
            "difficult_terms": terms_for_language(request.user, text.source_language),
            "quizzes": quizzes_with_stats(text, request.user),
        },
    )


@login_required
@require_GET
def chunk_audio(request, pk):
    chunk = get_object_or_404(TextChunk.objects.select_related("text"), pk=pk, text__owner=request.user)
    try:
        path = tts.get_chunk_audio(chunk)
    except tts.TTSError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    response = FileResponse(open(path, "rb"), content_type="audio/mpeg")
    response["Cache-Control"] = "private, max-age=86400"
    return response


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------
@login_required
def tag_list(request):
    form = TagForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        tag = form.save()
        messages.success(request, f"Tag “{tag.name}” criada.")
        return redirect("library:tag_list")
    tags = Tag.objects.filter(owner=request.user).annotate(text_count=Count("texts"))
    return render(request, "library/tag_list.html", {"form": form, "tags": tags})


@login_required
@require_POST
def tag_update(request, pk):
    tag = get_object_or_404(Tag, pk=pk, owner=request.user)
    old_name = tag.name
    form = TagForm(request.POST, instance=tag, owner=request.user)
    if form.is_valid():
        form.save()
        messages.success(request, f"Tag “{old_name}” renomeada para “{tag.name}”.")
    else:
        messages.error(request, " ".join(form.errors.get("name", ["Nome inválido."])))
    return redirect("library:tag_list")


@login_required
def tag_delete(request, pk):
    tag = get_object_or_404(Tag.objects.annotate(text_count=Count("texts")), pk=pk, owner=request.user)
    if request.method == "POST":
        name = tag.name
        tag.delete()  # remove apenas as associações; os textos permanecem
        messages.success(request, f"Tag “{name}” excluída. Os textos associados foram mantidos.")
        return redirect("library:tag_list")
    return render(request, "library/tag_confirm_delete.html", {"tag": tag})
