"""
Registro de idiomas suportados.

Para adicionar um idioma basta incluir uma entrada em `SUPPORTED_LANGUAGES`:
o código ISO 639-1 é o valor armazenado no banco, `name` é o nome exibido,
`english_name` é usado nos prompts da IA, `tts_locale` é a tag BCP 47
usada pelo Text-to-Speech do navegador e `gtts_lang` o código do gTTS
(áudio gerado no servidor).
"""
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    english_name: str
    tts_locale: str
    gtts_lang: str = ""

    @property
    def server_tts_lang(self):
        return self.gtts_lang or self.code


SUPPORTED_LANGUAGES = {
    lang.code: lang
    for lang in [
        Language("pt", "Português", "Brazilian Portuguese", "pt-BR"),
        Language("en", "Inglês", "English", "en-US"),
        Language("es", "Espanhol", "Spanish", "es-ES"),
        Language("fr", "Francês", "French", "fr-FR"),
        Language("de", "Alemão", "German", "de-DE"),
        Language("it", "Italiano", "Italian", "it-IT"),
        Language("ja", "Japonês", "Japanese", "ja-JP"),
        Language("zh", "Chinês (mandarim)", "Mandarin Chinese (simplified)", "zh-CN", gtts_lang="zh-CN"),
        Language("ko", "Coreano", "Korean", "ko-KR"),
        Language("ru", "Russo", "Russian", "ru-RU"),
    ]
}

DEFAULT_TRANSLATION_LANGUAGE = getattr(settings, "DEFAULT_TRANSLATION_LANGUAGE", "pt")
DEFAULT_STUDY_LANGUAGE = getattr(settings, "DEFAULT_STUDY_LANGUAGE", "en")

LANGUAGE_CHOICES = [(lang.code, lang.name) for lang in SUPPORTED_LANGUAGES.values()]


def get_language(code):
    return SUPPORTED_LANGUAGES.get(code) or Language(code, code, code, code)


def language_name(code):
    return get_language(code).name
