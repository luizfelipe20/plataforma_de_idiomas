import json
import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_GET, require_POST

from apps.core.ai import AIServiceError
from apps.core.http import ranged_file_response
from apps.quizzes import services as quiz_services

from . import pipeline, services
from .forms import UploadForm
from .models import MediaSegment, MediaSource, ProcessingStep, TranscriptCue

FILE_KINDS = {
    # kind: (campo, nome para download)
    "original": ("original", "video"),
    "audio-original": ("audio_original", "audio-original"),
    "audio-tratado": ("audio_clean", "audio-tratado"),
}
SUBTITLE_TYPES = {"vtt": "text/vtt", "srt": "application/x-subrip", "json": "application/json"}


def _user_media(request, pk):
    return get_object_or_404(MediaSource, pk=pk, owner=request.user)


def _user_segment(request, pk, index):
    return get_object_or_404(
        MediaSegment.objects.select_related("media"), media__pk=pk, media__owner=request.user, index=index
    )


def _status_payload(media):
    return {
        "id": media.pk,
        "status": media.status,
        "status_label": media.get_status_display(),
        "progress": media.progress,
        "upload_percent": media.upload_percent,
        "received_bytes": media.received_bytes,
        "size_bytes": media.size_bytes,
        "current_step": media.current_step,
        "current_step_label": media.current_step_label,
        "error": media.error_message,
        "detail_url": media.get_absolute_url(),
    }


def _base_name(media):
    return slugify(media.title)[:60] or f"video-{media.pk}"


# ---------------------------------------------------------------------------
# Lista e upload
# ---------------------------------------------------------------------------
@login_required
def media_list(request):
    media = MediaSource.objects.filter(owner=request.user).annotate(segment_count=Count("segments"))
    return render(
        request,
        "videos/media_list.html",
        {
            "media_list": media,
            "form": UploadForm(),
            "max_hours": settings.VIDEO_MAX_DURATION_SECONDS / 3600,
            "max_gb": settings.VIDEO_MAX_UPLOAD_BYTES / 1024**3,
            "allowed_extensions": settings.VIDEO_ALLOWED_EXTENSIONS,
            "upload_config": {
                "start_url": reverse("videos:upload_start"),
                "status_url": reverse("videos:media_status"),
                "max_duration": settings.VIDEO_MAX_DURATION_SECONDS,
                "max_bytes": settings.VIDEO_MAX_UPLOAD_BYTES,
                "extensions": settings.VIDEO_ALLOWED_EXTENSIONS,
            },
        },
    )


@login_required
@require_POST
def upload_start(request):
    """Cria o vídeo e devolve onde enviar os blocos do arquivo (chamada via fetch)."""
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Requisição inválida."}, status=400)
    form = UploadForm(data)
    if not form.is_valid():
        return JsonResponse({"error": " ".join(e for errs in form.errors.values() for e in errs)}, status=400)
    duration = data.get("duration")
    try:
        media = services.start_upload(
            request.user,
            title=form.cleaned_data["title"],
            filename=str(data.get("filename") or ""),
            size=data.get("size") if isinstance(data.get("size"), int) else -1,
            language=form.cleaned_data["language"],
            target_language=form.cleaned_data["target_language"],
            duration=float(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
        )
    except services.UploadError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse(
        {
            "id": media.pk,
            "chunk_url": reverse("videos:upload_chunk", args=[media.pk]),
            "chunk_size": settings.VIDEO_UPLOAD_CHUNK_BYTES,
            "received_bytes": 0,
            "detail_url": media.get_absolute_url(),
        },
        status=201,
    )


@login_required
@require_POST
def upload_chunk(request, pk):
    """Recebe um bloco binário (corpo da requisição) que começa no byte `?offset=`."""
    media = _user_media(request, pk)
    try:
        offset = int(request.GET.get("offset", ""))
        # X-Chunk-Length cobre proxies que repassam o corpo sem Content-Length.
        length = int(request.headers.get("Content-Length") or request.headers.get("X-Chunk-Length", ""))
    except ValueError:
        return JsonResponse({"error": "Bloco inválido."}, status=400)
    try:
        media = services.append_chunk(media, offset, request, length)
    except services.UploadError as exc:
        media.refresh_from_db()
        # 409: o cliente retoma a partir de received_bytes.
        return JsonResponse({"error": str(exc), **_status_payload(media)}, status=409)
    return JsonResponse(_status_payload(media))


@login_required
@require_GET
def media_status(request):
    """Situação de um ou mais vídeos (?ids=1,2), consultada periodicamente pela interface."""
    ids = [int(i) for i in request.GET.get("ids", "").split(",") if i.isdigit()][:50]
    media = MediaSource.objects.filter(owner=request.user, pk__in=ids)
    return JsonResponse({"items": [_status_payload(m) for m in media]})


# ---------------------------------------------------------------------------
# Detalhe, histórico e ações
# ---------------------------------------------------------------------------
@login_required
def media_detail(request, pk):
    media = _user_media(request, pk)
    segments = media.segments.annotate(cue_count=Count("cues"))
    return render(
        request,
        "videos/media_detail.html",
        {
            "media": media,
            "quiz_presets": services.QUIZ_PRESETS,
            "media_texts": media.texts.prefetch_related("quizzes").order_by("-created_at"),
            "steps": media.steps.all(),
            "segments": segments,
            "cue_count": TranscriptCue.objects.filter(media=media).count(),
            "status_url": reverse("videos:media_status"),
            "upload_config": {
                "chunk_url": reverse("videos:upload_chunk", args=[media.pk]),
                "chunk_size": settings.VIDEO_UPLOAD_CHUNK_BYTES,
                "received_bytes": media.received_bytes,
                "size_bytes": media.size_bytes,
                "filename": media.original_filename,
                "detail_url": media.get_absolute_url(),
            },
        },
    )


@login_required
@require_POST
def media_retry(request, pk):
    media = _user_media(request, pk)
    try:
        pipeline.retry(media)
    except pipeline.StepError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Processamento reenviado para a fila; ele continua da etapa que falhou.")
    return redirect(media)


@login_required
def media_delete(request, pk):
    media = _user_media(request, pk)
    if request.method == "POST":
        if media.status == MediaSource.Status.PROCESSING:
            messages.error(request, "Aguarde o fim da etapa em andamento para excluir este vídeo.")
            return redirect(media)
        title = media.title
        services.delete_media(media)
        messages.success(request, f"“{title}” e todos os arquivos gerados foram excluídos.")
        return redirect("videos:media_list")
    return render(
        request,
        "videos/media_confirm_delete.html",
        {"media": media, "segment_count": media.segments.count(), "text_count": media.texts.count()},
    )


# ---------------------------------------------------------------------------
# Arquivos e legendas
# ---------------------------------------------------------------------------
@login_required
@require_GET
def media_file(request, pk, kind):
    media = _user_media(request, pk)
    if kind not in FILE_KINDS:
        raise Http404
    field_name, label = FILE_KINDS[kind]
    field = getattr(media, field_name)
    if not field or (field_name == "original" and media.status == MediaSource.Status.UPLOADING):
        raise Http404("Arquivo ainda não disponível.")
    path = pipeline.local_path(field.name)
    if not os.path.exists(path):
        raise Http404("Arquivo não encontrado.")
    ext = os.path.splitext(path)[1]
    return ranged_file_response(
        request, path, filename=f"{_base_name(media)}-{label}{ext}", as_attachment=request.GET.get("baixar") == "1"
    )


def _subtitles_response(fmt, cues, offset, filename, data=None):
    if fmt not in SUBTITLE_TYPES:
        raise Http404
    if fmt == "json":
        body = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        body = (services.to_vtt if fmt == "vtt" else services.to_srt)(cues, offset)
    response = HttpResponse(body, content_type=f"{SUBTITLE_TYPES[fmt]}; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}.{fmt}"'
    return response


@login_required
@require_GET
def media_subtitles(request, pk, fmt):
    """Legendas do vídeo inteiro, com tempos do vídeo original."""
    media = _user_media(request, pk)
    segments = media.segments.prefetch_related("cues")
    data = services.transcript_data(media, segments) if fmt == "json" else None
    return _subtitles_response(fmt, TranscriptCue.objects.filter(media=media), 0.0, _base_name(media), data)


@login_required
@require_GET
def segment_audio(request, pk, index):
    segment = _user_segment(request, pk, index)
    if not segment.audio:
        raise Http404("Áudio do segmento ainda não gerado.")
    return ranged_file_response(
        request,
        pipeline.local_path(segment.audio.name),
        content_type="audio/mpeg",
        filename=f"{_base_name(segment.media)}-segmento-{segment.number:03d}.mp3",
        as_attachment=request.GET.get("baixar") == "1",
    )


@login_required
@require_GET
def segment_subtitles(request, pk, index, fmt):
    """Legendas do segmento, com tempos relativos ao áudio do segmento (sincronizam com o MP3 dele)."""
    segment = _user_segment(request, pk, index)
    data = services.transcript_data(segment.media, [segment]) if fmt == "json" else None
    name = f"{_base_name(segment.media)}-segmento-{segment.number:03d}"
    return _subtitles_response(fmt, segment.cues.all(), segment.start_seconds, name, data)


# ---------------------------------------------------------------------------
# Criação de quizzes a partir do vídeo
# ---------------------------------------------------------------------------
@login_required
@require_POST
def media_quiz_create(request, pk):
    """
    Cria (ou reaproveita) o texto da fonte escolhida — vídeo inteiro, segmento ou intervalo
    de falas — e gera o quiz, já pronto para responder.
    """
    media = _user_media(request, pk)
    back = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        back = media.get_absolute_url()
    if media.status != MediaSource.Status.READY:
        messages.error(request, "O vídeo ainda não terminou de ser processado.")
        return redirect(back)
    preset = services.QUIZ_PRESETS.get(request.POST.get("preset"), services.QUIZ_PRESETS["mixed"])
    spec = request.POST.get("source") or "all"
    if spec == "range":
        start, end = services.parse_time(request.POST.get("range_start")), services.parse_time(request.POST.get("range_end"))
        if start is None or end is None:
            messages.error(request, "Informe o início e o fim do intervalo, por exemplo 12:30 e 18:00.")
            return redirect(back)
        spec = f"range:{start}-{end}"
    try:
        text, _created = services.text_for_source(media, spec)
    except services.SourceError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    if request.POST.get("custom"):
        messages.info(request, "Escolha os tipos e as quantidades de perguntas do quiz.")
        return redirect("quizzes:quiz_new", text_pk=text.pk)
    try:
        quiz = quiz_services.generate_quiz(text, plan=preset[2], title=request.POST.get("title", "")[:120])
    except (AIServiceError, quiz_services.QuizValidationError) as exc:
        messages.error(request, f"Não foi possível gerar o quiz: {exc}")
        return redirect(back)
    total = quiz.questions.count()
    note = f" Observação: {quiz.generation_notes}" if quiz.generation_notes else ""
    messages.success(request, f"{quiz.label} criado com {total} pergunta{'s' if total != 1 else ''}.{note}")
    return redirect(quiz)


# ---------------------------------------------------------------------------
# Segmento: player, legendas sincronizadas e revisão
# ---------------------------------------------------------------------------
@login_required
def segment_detail(request, pk, index):
    segment = _user_segment(request, pk, index)
    media = segment.media
    cues = list(segment.cues.all())
    siblings = media.segments.values_list("index", flat=True)
    return render(
        request,
        "videos/segment_detail.html",
        {
            "media": media,
            "segment": segment,
            "cues": cues,
            "edited_count": sum(c.edited for c in cues),
            "previous_index": index - 1 if index - 1 in siblings else None,
            "next_index": index + 1 if index + 1 in siblings else None,
            "cue_data": [
                {"id": c.pk, "start": round(c.start_seconds - segment.start_seconds, 3),
                 "end": round(c.end_seconds - segment.start_seconds, 3)}
                for c in cues
            ],
            "editing": request.GET.get("revisar") == "1",
            "quiz_presets": services.QUIZ_PRESETS,
        },
    )


@login_required
@require_POST
def segment_review(request, pk, index):
    """Salva a revisão manual das falas do segmento (ou desfaz todas as edições)."""
    segment = _user_segment(request, pk, index)
    cues = list(segment.cues.all())
    if request.POST.get("action") == "restore":
        for cue in cues:
            cue.text = cue.original_text
        TranscriptCue.objects.bulk_update(cues, ["text"])
        messages.success(request, "As falas voltaram ao texto reconhecido automaticamente.")
        return redirect(segment)
    changed = 0
    with transaction.atomic():
        for cue in cues:
            new_text = " ".join(request.POST.get(f"cue-{cue.pk}", cue.text).split())
            if not new_text:
                new_text = cue.original_text  # fala apagada: volta ao texto reconhecido
            if new_text != cue.text:
                cue.text = new_text
                cue.save(update_fields=["text"])
                changed += 1
    messages.success(request, f"Revisão salva: {changed} fala{'s' if changed != 1 else ''} alterada{'s' if changed != 1 else ''}.")
    return redirect(segment)


@login_required
@require_POST
def segment_retranscribe(request, pk, index):
    """Agenda nova transcrição automática do segmento (substitui as falas, inclusive revisadas)."""
    segment = _user_segment(request, pk, index)
    media = segment.media
    if media.is_busy:
        messages.error(request, "O vídeo já está em processamento. Aguarde terminar.")
        return redirect(segment)
    MediaSegment.objects.filter(pk=segment.pk).update(transcript_status=MediaSegment.TranscriptStatus.PENDING)
    media.steps.filter(name=ProcessingStep.Name.TRANSCRIBE).update(status=ProcessingStep.Status.PENDING)
    pipeline.enqueue(media)
    messages.success(request, f"Segmento {segment.number} enviado para nova transcrição.")
    return redirect(segment)
