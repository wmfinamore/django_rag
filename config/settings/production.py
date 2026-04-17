"""
Settings de produção.
Ativo via DJANGO_SETTINGS_MODULE=config.settings.production
"""

from .base import *  # noqa: F401, F403

import environ

_env = environ.Env()

# ---------------------------------------------------------------------------
# Modo debug — NUNCA True em produção
# ---------------------------------------------------------------------------

DEBUG = False

ALLOWED_HOSTS = _env.list("ALLOWED_HOSTS", default=[])

# ---------------------------------------------------------------------------
# Segurança HTTPS
# ---------------------------------------------------------------------------

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000          # 1 ano
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True

# ---------------------------------------------------------------------------
# E-mail — configurar SMTP em produção
# ---------------------------------------------------------------------------

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = _env("EMAIL_HOST", default="localhost")
EMAIL_PORT = _env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = _env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = _env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = _env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = _env("DEFAULT_FROM_EMAIL", default="noreply@example.com")

# ---------------------------------------------------------------------------
# Logging — erros para arquivo / syslog
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
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
