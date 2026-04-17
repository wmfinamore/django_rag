"""
Settings base — compartilhado entre todos os ambientes.
Nunca use este módulo diretamente; importe development.py ou production.py.
"""

from pathlib import Path

import environ

# ---------------------------------------------------------------------------
# Caminhos
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Leitura do .env
# ---------------------------------------------------------------------------

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Segurança
# ---------------------------------------------------------------------------

SECRET_KEY = env("SECRET_KEY")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Apps instalados
# ---------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    #"rest_framework",
    #"channels",
    "mozilla_django_oidc",
]

LOCAL_APPS = [
    #"apps.core",
    "apps.accounts",
    #"apps.knowledge",
    #"apps.documents",
    #"apps.chat",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# URLs que o SessionRefresh deve ignorar (callback e rotas do próprio OIDC)
OIDC_EXEMPT_URLS = [
    r"^rag/oidc/",
    r"^oidc/",
]

# ---------------------------------------------------------------------------
# URLs / WSGI / ASGI
# ---------------------------------------------------------------------------

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

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
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------

DATABASES = {
    "default": env.db("DATABASE_URL"),
}
DATABASES["default"]["OPTIONS"] = {"connect_timeout": 10}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Usuário customizado
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.CustomUser"

# ---------------------------------------------------------------------------
# Autenticação — OIDC + fallback local
# ---------------------------------------------------------------------------

AUTHENTICATION_BACKENDS = [
    "apps.accounts.oidc_backend.GroupSyncOIDCBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# Keycloak OIDC (mozilla-django-oidc)
OIDC_RP_CLIENT_ID = env("OIDC_RP_CLIENT_ID")
OIDC_RP_CLIENT_SECRET = env("OIDC_RP_CLIENT_SECRET")
OIDC_OP_AUTHORIZATION_ENDPOINT = env("OIDC_OP_AUTHORIZATION_ENDPOINT")
OIDC_OP_TOKEN_ENDPOINT = env("OIDC_OP_TOKEN_ENDPOINT")
OIDC_OP_USER_ENDPOINT = env("OIDC_OP_USER_ENDPOINT")
OIDC_OP_JWKS_ENDPOINT = env("OIDC_OP_JWKS_ENDPOINT")
# Endpoint de logout do Keycloak (end_session_endpoint do OIDC Discovery)
OIDC_OP_LOGOUT_ENDPOINT = env(
    "OIDC_OP_LOGOUT_ENDPOINT",
    default="",  # preenchido automaticamente pelo mozilla-django-oidc via JWKS discovery
)
OIDC_RP_SIGN_ALGO = "RS256"
# Persiste o id_token na sessão para que o logout federado possa enviar
# o id_token_hint ao end_session_endpoint do Keycloak.
OIDC_STORE_ID_TOKEN = True

# Tempo de vida do token OIDC em cache (segundos). O SessionRefresh middleware
# verifica expiração a cada request; renovação ocorre quando restarem menos de
# OIDC_RENEW_ID_TOKEN_EXPIRY_SECONDS segundos de validade.
OIDC_RENEW_ID_TOKEN_EXPIRY_SECONDS = env.int(
    "OIDC_RENEW_ID_TOKEN_EXPIRY_SECONDS", default=60
)

# Redireciona para home após login/logout via OIDC
# A URL base do projeto é /rag/, portanto apontamos para lá.
LOGIN_REDIRECT_URL = "/rag/"
LOGOUT_REDIRECT_URL = "/rag/"

# URL de login usada pelo decorator @login_required
LOGIN_URL = "/rag/oidc/authenticate/"

# ---------------------------------------------------------------------------
# Cache — Redis
# ---------------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/0"),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# ---------------------------------------------------------------------------
# Django Channels — channel layer (Redis)
# ---------------------------------------------------------------------------

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [env("REDIS_URL", default="redis://localhost:6379/0")],
        },
    },
}

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "America/Sao_Paulo"
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 min

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# ---------------------------------------------------------------------------
# Internacionalização
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Arquivos estáticos e de mídia
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# Ollama (LLM)
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", default="http://localhost:11434")
OLLAMA_LLM_MODEL = env("OLLAMA_LLM_MODEL", default="llama3.2:3b")
OLLAMA_NUM_CTX = env.int("OLLAMA_NUM_CTX", default=2048)
OLLAMA_NUM_THREAD = env.int("OLLAMA_NUM_THREAD", default=4)
OLLAMA_TEMPERATURE = env.float("OLLAMA_TEMPERATURE", default=0.3)

# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = env("EMBEDDING_MODEL", default="all-MiniLM-L6-v2")

# ---------------------------------------------------------------------------
# Pipeline RAG
# ---------------------------------------------------------------------------

RAG_TOP_K = env.int("RAG_TOP_K", default=4)
RAG_RERANK_FACTOR = env.int("RAG_RERANK_FACTOR", default=3)
RAG_RERANKER_MODEL = env(
    "RAG_RERANKER_MODEL",
    default="cross-encoder/ms-marco-MiniLM-L-6-v2",
)
RAG_SEMANTIC_BREAKPOINT = env("RAG_SEMANTIC_BREAKPOINT", default="percentile")
RAG_CHUNK_SIZE = env.int("RAG_CHUNK_SIZE", default=500)
RAG_CHUNK_OVERLAP = env.int("RAG_CHUNK_OVERLAP", default=50)