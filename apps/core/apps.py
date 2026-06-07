import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    name = 'apps.core'

    def ready(self):
        from django.conf import settings

        from apps.core.rag_service import _get_embedding_model
        from apps.core.reranker import _get_cross_encoder

        try:
            _get_embedding_model(settings.EMBEDDING_MODEL)
        except Exception:
            logger.exception("Falha ao pré-carregar o modelo de embedding.")

        try:
            _get_cross_encoder(settings.RAG_RERANKER_MODEL)
        except Exception:
            logger.exception("Falha ao pré-carregar o CrossEncoder.")
