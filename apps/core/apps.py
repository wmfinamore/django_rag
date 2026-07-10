import json
import logging
import threading
import urllib.request

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

        threading.Thread(target=_warmup_ollama, daemon=True).start()


def _warmup_ollama():
    """
    Envia uma requisição keep-alive ao Ollama para manter o modelo carregado
    na memória e eliminar o cold start na primeira query do usuário.
    Executado em background para não bloquear a inicialização do servidor.
    """
    from django.conf import settings

    base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    model = getattr(settings, "OLLAMA_LLM_MODEL", "llama3.2:3b")
    keep_alive = getattr(settings, "OLLAMA_KEEP_ALIVE", "-1")

    # Ollama aceita keep_alive como número (segundos; -1 = indefinido) ou
    # string de duração com unidade ("30m", "1h"). String numérica sem
    # unidade ("-1") falha no parse e gera HTTP 400 — converte para int.
    if isinstance(keep_alive, str) and keep_alive.lstrip("-").isdigit():
        keep_alive = int(keep_alive)

    payload = json.dumps({
        "model": model,
        "keep_alive": keep_alive,
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        f"{base_url}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=60):
            logger.info(
                "Ollama warm-up concluído: modelo '%s' mantido na memória (keep_alive=%s).",
                model,
                keep_alive,
            )
    except Exception:
        logger.warning(
            "Ollama warm-up falhou — servidor pode estar indisponível.",
            exc_info=True,
        )
