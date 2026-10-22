from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from apps.core.languages import LANGUAGE_CHOICES
from apps.library.models import Tag, Text
from apps.quizzes.models import StudyMode
from apps.quizzes.services import quizzes_with_stats
from apps.tracking import services as tracking

from . import services


@login_required
def index(request):
    filters = services.parse_filters(request.GET)
    data = services.build_dashboard(request.user, filters)
    tracking.expire_stale_sessions(user=request.user)
    context = {
        **data,
        "filters": filters,
        "periods": [(k, v[0]) for k, v in services.PERIOD_PRESETS.items()],
        "language_choices": LANGUAGE_CHOICES,
        "all_tags": Tag.objects.filter(owner=request.user),
        "all_texts": Text.objects.filter(owner=request.user).order_by("title").only("id", "title"),
        "time": tracking.time_summary(request.user, current_access=request.access_session),
        "daily_series": tracking.daily_study_series(request.user),
        "weekly_series": tracking.weekly_study_series(request.user),
        "recent_sessions": request.user.access_sessions.all()[:5],
        "review_threshold": services.settings.DASHBOARD_REVIEW_THRESHOLD,
        "min_attempts": services.settings.DASHBOARD_MIN_ATTEMPTS,
    }
    return render(request, "dashboard/index.html", context)


@login_required
def text_report(request, pk):
    text = get_object_or_404(Text.objects.prefetch_related("tags"), pk=pk, owner=request.user)
    filters = services.parse_filters(request.GET)
    filters.text_id, filters.tag_ids, filters.language = None, [], ""
    report = services.text_report(request.user, text, filters)
    return render(
        request,
        "dashboard/text_report.html",
        {
            "text": text,
            "filters": filters,
            "periods": [(k, v[0]) for k, v in services.PERIOD_PRESETS.items()],
            "quiz_stats": quizzes_with_stats(text, request.user),
            "study_modes": StudyMode.choices,
            **report,
        },
    )
