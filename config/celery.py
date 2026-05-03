"""
Configuração do Celery para o projeto django_rag.

Uso:
    uv run celery -A config worker -l info
    uv run celery -A config beat -l info
"""

import os

from celery import Celery

# Define o módulo de settings padrão para o Celery
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("django_rag")

# Lê as configurações do Django com o prefixo CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Descobre tasks automaticamente em todos os INSTALLED_APPS
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Task de diagnóstico — imprime o request atual."""
    print(f"Request: {self.request!r}")
