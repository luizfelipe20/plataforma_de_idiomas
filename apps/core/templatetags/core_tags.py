from django import template

from apps.core.languages import get_language
from apps.core.utils import format_duration

register = template.Library()


@register.filter
def lang_name(code):
    return get_language(code).name


@register.filter
def tts_locale(code):
    return get_language(code).tts_locale


@register.filter
def duration(seconds):
    """Formata segundos como HH:MM:SS."""
    return format_duration(seconds)


@register.filter
def human_duration(seconds):
    """Formata segundos como '1h 05min' / '12min' / '40s'."""
    seconds = int(seconds or 0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}min"
    if minutes:
        return f"{minutes}min"
    return f"{secs}s"


@register.filter
def score_class(score):
    """Classe CSS para colorir uma pontuação."""
    if score is None:
        return "score-none"
    if score >= 80:
        return "score-good"
    if score >= 60:
        return "score-mid"
    return "score-bad"


@register.simple_tag(takes_context=True)
def query_transform(context, **kwargs):
    """Mantém a querystring atual alterando apenas os parâmetros informados."""
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    return params.urlencode()
