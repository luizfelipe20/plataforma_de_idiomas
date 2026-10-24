import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.ai import AIServiceError
from apps.library.models import Text

from . import services
from .forms import MeaningForm, PracticeTextForm, TermSelectionForm
from .models import DifficultTerm


def _back(request):
    """Volta para a página de origem (`next`), se for deste site; senão, para a lista."""
    target = request.POST.get("next") or ""
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(target)
    return redirect("vocabulary:term_list")


def _form_error(form):
    return " ".join(e for errors in form.errors.values() for e in errors)


@login_required
def term_list(request):
    qs = DifficultTerm.objects.filter(owner=request.user)
    languages = sorted(set(qs.values_list("language", flat=True)))
    language = request.GET.get("idioma") or ""
    if language:
        qs = qs.filter(language=language)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(term__icontains=q) | Q(meaning__icontains=q) | Q(context__icontains=q))
    # Consultas com agregação ignoram o Meta.ordering: a ordem é explícita.
    qs = (
        qs.select_related("source_text")
        .annotate(practice_count=Count("practice_texts"))
        .order_by(*DifficultTerm._meta.ordering)
    )
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "vocabulary/term_list.html",
        {
            "page": page,
            "languages": languages,
            "language": language,
            "q": q,
            "practice_form": PracticeTextForm(owner=request.user),
            "max_practice_terms": services.MAX_PRACTICE_TERMS,
        },
    )


@login_required
def term_detail(request, pk):
    term = get_object_or_404(DifficultTerm.objects.select_related("source_text"), pk=pk, owner=request.user)
    return render(
        request,
        "vocabulary/term_detail.html",
        {
            "term": term,
            "practice_texts": term.practice_texts.order_by("-created_at"),
            "texts_with_term": services.texts_with_term(term),
            "practice_form": PracticeTextForm(owner=request.user),
        },
    )


@login_required
@require_POST
def term_mark(request):
    """Marca a seleção feita no texto como termo difícil (chamada via fetch)."""
    try:
        data = json.loads(request.body or b"{}")
        text_id = int(data.get("text_id"))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return JsonResponse({"error": "Requisição inválida."}, status=400)
    text = get_object_or_404(Text, pk=text_id, owner=request.user)
    try:
        term, created = services.mark_term(request.user, data.get("term"), text, data.get("context") or "")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(
        {
            "id": term.pk,
            "term": term.term,
            "meaning": term.meaning,
            "url": term.get_absolute_url(),
            "times_marked": term.times_marked,
            "created": created,
        },
        status=201 if created else 200,
    )


@login_required
@require_POST
def term_update(request, pk):
    term = get_object_or_404(DifficultTerm, pk=pk, owner=request.user)
    form = MeaningForm(request.POST, instance=term)
    if form.is_valid():
        form.save()
        messages.success(request, f"Significado de “{term.term}” atualizado.")
    else:
        messages.error(request, _form_error(form))
    return _back(request)


@login_required
@require_POST
def term_delete(request, pk):
    term = get_object_or_404(DifficultTerm, pk=pk, owner=request.user)
    term.delete()
    messages.success(request, f"“{term.term}” foi removido da lista.")
    return _back(request)


@login_required
@require_POST
def term_explain(request):
    form = TermSelectionForm(request.POST, owner=request.user)
    if not form.is_valid():
        messages.error(request, _form_error(form))
        return _back(request)
    try:
        count = services.explain_terms(form.cleaned_data["terms"])
    except AIServiceError as exc:
        messages.error(request, f"Não foi possível explicar os termos: {exc}")
    else:
        messages.success(request, f"{count} termo{'s' if count != 1 else ''} explicado{'s' if count != 1 else ''}.")
    return _back(request)


@login_required
@require_POST
def practice_create(request):
    form = PracticeTextForm(request.POST, owner=request.user)
    if not form.is_valid():
        messages.error(request, _form_error(form))
        return _back(request)
    try:
        text, missing = services.create_practice_text(
            request.user,
            form.cleaned_data["terms"],
            length=form.cleaned_data["length"],
            topic=form.cleaned_data["topic"],
        )
    except AIServiceError as exc:
        messages.error(request, f"Não foi possível gerar o texto de prática: {exc}")
        return _back(request)
    if missing:
        listed = ", ".join(f"“{t.term}”" for t in missing)
        messages.warning(request, f"Texto de prática criado, mas a IA não usou: {listed}.")
    else:
        messages.success(request, "Texto de prática criado com todos os termos selecionados.")
    return redirect(text)
