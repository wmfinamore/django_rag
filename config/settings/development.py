"""
Settings de desenvolvimento.
Ativo via DJANGO_SETTINGS_MODULE=config.settings.development
"""

from .base import *  # noqa: F401, F403

# ---------------------------------------------------------------------------
# Modo debug
# ---------------------------------------------------------------------------

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0"]

# ---------------------------------------------------------------------------
# Apps e middleware exclusivos de dev
# ---------------------------------------------------------------------------

INSTALLED_APPS += ["debug_toolbar"]  # noqa: F405

MIDDLEWARE = [
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    *MIDDLEWARE,  # noqa: F405
]

INTERNAL_IPS = ["127.0.0.1"]

# ---------------------------------------------------------------------------
# Banco de dados — fallback SQLite se DATABASE_URL não estiver definido
# ---------------------------------------------------------------------------
# Para usar PostgreSQL, defina DATABASE_URL no .env:
#   DATABASE_URL=postgresql://postgres:postgres@localhost:5432/django_rag
#
# O env.db() em base.py já lê DATABASE_URL; se não existir, django-environ
# levantará ImproperlyConfigured. Descomente o bloco abaixo para usar SQLite
# como fallback local sem precisar do .env:
#
# import environ, pathlib
# _env = environ.Env()
# DATABASES = {
#     "default": _env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
# }

# ---------------------------------------------------------------------------
# E-mail — saída no terminal
# ---------------------------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ---------------------------------------------------------------------------
# Logging detalhado
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "DEBUG",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.db.queries": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
