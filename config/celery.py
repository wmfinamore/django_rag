"""
Configuração do Celery para o projeto django_rag.

Uso:
    uv run celery -A config worker -l info
    uv run celery -A config beat -l info
"""

import logging
import os

from celery import Celery
from celery.signals import worker_process_init

# Define o módulo de settings padrão para o Celery
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("django_rag")

# Lê as configurações do Django com o prefixo CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Descobre tasks automaticamente em todos os INSTALLED_APPS
app.autodiscover_tasks()


@worker_process_init.connect
def preload_ml_models(sender=None, **kwargs):
    """
    Pré-carrega modelos de ML no worker Celery para eliminar o cold start
    na primeira task de indexação. Espelha o CoreConfig.ready() do servidor Django.
    """
    from django.conf import settings

    logger = logging.getLogger(__name__)

    try:
        from apps.core.rag_service import _get_embedding_model
        _get_embedding_model(settings.EMBEDDING_MODEL)
        logger.info("Worker: modelo de embedding pré-carregado.")
    except Exception:
        logger.exception("Worker: falha ao pré-carregar modelo de embedding.")

    try:
        from apps.core.rag_service import get_langchain_embeddings
        get_langchain_embeddings()
        logger.info("Worker: LangChain embeddings pré-carregados.")
    except Exception:
        logger.exception("Worker: falha ao pré-carregar LangChain embeddings.")

    try:
        from apps.core.privacy_filter import _get_engines
        _get_engines()
        logger.info("Worker: Presidio pré-carregado.")
    except Exception:
        logger.exception("Worker: falha ao pré-carregar Presidio.")


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Task de diagnóstico — imprime o request atual."""
    print(f"Request: {self.request!r}")
