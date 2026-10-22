from .languages import SUPPORTED_LANGUAGES


def languages(request):
    return {"supported_languages": SUPPORTED_LANGUAGES.values()}
