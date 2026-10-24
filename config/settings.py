"""
Configurações do projeto LinguaLab.

Todos os valores sensíveis ou dependentes de ambiente são lidos de variáveis
de ambiente (veja `.env.example`).
"""
import os
import sys
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY") or (
    "dev-insecure-key-change-me" if DEBUG else None
)
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY deve ser definida quando DJANGO_DEBUG=False.")

ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.accounts",
    "apps.library",
    "apps.quizzes",
    "apps.tracking",
    "apps.vocabulary",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.tracking.middleware.AccessSessionMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.languages",
                "apps.tracking.context_processors.access_session",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite por padrão; defina DATABASE_URL=postgres://... para PostgreSQL.
# Variável vazia também conta como "não definida" (ex.: `DATABASE_URL=` no .env).
DATABASES = {
    "default": dj_database_url.parse(
        os.getenv("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=env_int("DB_CONN_MAX_AGE", 60),
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "America/Sao_Paulo")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
WHITENOISE_USE_FINDERS = DEBUG

# Arquivos gerados pela aplicação (áudios do TTS). Não são servidos publicamente:
# o acesso passa por uma view que verifica o dono do texto.
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT") or BASE_DIR / "media")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:index"
LOGOUT_REDIRECT_URL = "accounts:login"

# Segurança adicional em produção
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # o JS lê o token para as chamadas fetch()
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
if not DEBUG and env_bool("DJANGO_HTTPS", True):
    # Desative com DJANGO_HTTPS=False para rodar sem DEBUG em HTTP local (ex.: Docker).
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = env_int("DJANGO_SECURE_HSTS_SECONDS", 3600)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"apps": {"handlers": ["console"], "level": os.getenv("APP_LOG_LEVEL", "INFO")}},
}

# ---------------------------------------------------------------------------
# Inteligência artificial
# ---------------------------------------------------------------------------
# AI_PROVIDER: "anthropic" (Claude) ou "mock" (offline, determinístico).
# Sem ANTHROPIC_API_KEY o padrão é "mock", para que a aplicação rode localmente.
AI_PROVIDER = os.getenv("AI_PROVIDER") or ("anthropic" if os.getenv("ANTHROPIC_API_KEY") else "mock")
if "test" in sys.argv[1:2]:
    AI_PROVIDER = "mock"  # os testes nunca chamam serviços externos
    os.environ["TTS_PROVIDER"] = "browser"
AI_MODEL = os.getenv("AI_MODEL", "claude-opus-5-5")
AI_TRANSLATION_EFFORT = os.getenv("AI_TRANSLATION_EFFORT", "low")
AI_QUIZ_EFFORT = os.getenv("AI_QUIZ_EFFORT", "medium")
AI_TIMEOUT_SECONDS = env_int("AI_TIMEOUT_SECONDS", 120)
AI_MAX_RETRIES = env_int("AI_MAX_RETRIES", 2)

# ---------------------------------------------------------------------------
# Áudio (Text-to-Speech)
# ---------------------------------------------------------------------------
# "gtts": MP3 gerado no servidor (Google Translate TTS) e guardado em cache; funciona
#         em qualquer navegador. Se a geração falhar, o player usa a voz do navegador.
# "browser": apenas a Web Speech API do navegador (sem chamadas externas).
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "gtts")
TTS_TIMEOUT_SECONDS = env_int("TTS_TIMEOUT_SECONDS", 20)

# ---------------------------------------------------------------------------
# Regras de negócio parametrizáveis
# ---------------------------------------------------------------------------
TEXT_CHUNK_MAX_CHARS = env_int("TEXT_CHUNK_MAX_CHARS", 800)
# Alternativas por pergunta nos quizzes novos (quizzes antigos guardam o próprio valor).
QUIZ_OPTIONS_PER_QUESTION = 5
# Limites da configuração de um quiz (soma das quantidades por tipo).
QUIZ_MAX_QUESTIONS = env_int("QUIZ_MAX_QUESTIONS", 20)
QUIZ_MAX_PER_KIND = env_int("QUIZ_MAX_PER_KIND", 10)
# Combinação usada pelo botão "Gerar quiz rápido" (tipo: quantidade).
QUIZ_DEFAULT_PLAN = {"comprehension": 2, "fill_blank": 1, "word_order": 1, "find_error": 1}
# Rodadas de geração para substituir perguntas reprovadas na validação.
QUIZ_GENERATION_ROUNDS = env_int("QUIZ_GENERATION_ROUNDS", 3)
# Similaridade (0–1) acima da qual uma pergunta é considerada repetida.
QUIZ_SIMILARITY_THRESHOLD = float(os.getenv("QUIZ_SIMILARITY_THRESHOLD", "0.85"))
# Tentativas "em andamento" mais antigas que isso são marcadas como abandonadas.
QUIZ_ATTEMPT_ABANDON_HOURS = env_int("QUIZ_ATTEMPT_ABANDON_HOURS", 24)

# Dashboard
DASHBOARD_REVIEW_THRESHOLD = env_int("DASHBOARD_REVIEW_THRESHOLD", 70)  # % de aproveitamento
DASHBOARD_MIN_ATTEMPTS = env_int("DASHBOARD_MIN_ATTEMPTS", 2)  # tentativas p/ resultado confiável
DASHBOARD_PRIOR_WEIGHT = env_int("DASHBOARD_PRIOR_WEIGHT", 2)  # peso da média geral no ranking

# ---------------------------------------------------------------------------
# Cronômetro / sessões de acesso
# ---------------------------------------------------------------------------
# Sem atividade do usuário por este tempo -> sessão encerrada por inatividade.
SESSION_INACTIVITY_TIMEOUT_SECONDS = env_int("SESSION_INACTIVITY_TIMEOUT_SECONDS", 30 * 60)
# Aviso exibido antes do encerramento por inatividade.
SESSION_INACTIVITY_WARNING_SECONDS = env_int("SESSION_INACTIVITY_WARNING_SECONDS", 60)
# Sem nenhum sinal do navegador (heartbeat/requisição) por este tempo -> sessão abandonada.
SESSION_ABANDON_TIMEOUT_SECONDS = env_int("SESSION_ABANDON_TIMEOUT_SECONDS", 10 * 60)
# Intervalo máximo entre dois sinais de atividade para que o tempo entre eles conte como "ativo".
SESSION_ACTIVE_GAP_SECONDS = env_int("SESSION_ACTIVE_GAP_SECONDS", 5 * 60)
# Intervalo do heartbeat enviado pelo navegador.
SESSION_HEARTBEAT_SECONDS = env_int("SESSION_HEARTBEAT_SECONDS", 60)
# Estudo: heartbeat e intervalo máximo creditado por batida.
STUDY_HEARTBEAT_SECONDS = env_int("STUDY_HEARTBEAT_SECONDS", 20)
STUDY_MAX_CREDIT_SECONDS = env_int("STUDY_MAX_CREDIT_SECONDS", 60)
# O sistema de sessões do Django expira junto com a regra de inatividade.
SESSION_COOKIE_AGE = env_int("DJANGO_SESSION_COOKIE_AGE", 14 * 24 * 3600)
