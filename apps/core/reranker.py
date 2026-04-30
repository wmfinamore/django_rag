"""
Reranker CrossEncoder para o pipeline RAG.

Usa o modelo ``cross-encoder/ms-marco-MiniLM-L-6-v2`` (sentence-transformers)
para reordenar candidatos de chunks recuperados pelo pgvector, melhorando
a relevância dos trechos enviados ao prompt do LLM.

O modelo é carregado uma única vez por processo via singleton lazy.

Uso::

    from apps.core.reranker import rerank

    # chunks: lista de strings (conteúdo dos chunks)
    # query: pergunta do usuário
    ranked = rerank(query="Qual é a política de férias?", chunks=chunks, top_k=4)
    # ranked: lista de strings, os top_k mais relevantes em ordem decrescente
"""

from __future__ import annotations

import logging
from functools import lru_cache

from django.conf import settings

from apps.core.exceptions import RerankerError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Singleton do CrossEncoder
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_cross_encoder(model_name: str):
    """
    Carrega o CrossEncoder do sentence-transformers.
    O ``@lru_cache`` garante que o modelo seja carregado apenas uma vez
    por processo (worker Celery ou Django).

    Levanta RerankerError se o sentence-transformers não estiver instalado
    ou se o modelo não puder ser carregado.
    """
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RerankerError(
            "sentence-transformers não está instalado. "
            "Execute: uv add sentence-transformers",
            model_name=model_name,
            original=exc,
        ) from exc

    logger.info("Carregando CrossEncoder '%s'…", model_name)
    try:
        model = CrossEncoder(model_name, device="cpu")
        logger.info("CrossEncoder '%s' carregado.", model_name)
        return model
    except Exception as exc:
        raise RerankerError(
            f"Falha ao carregar o CrossEncoder '{model_name}': {exc}",
            model_name=model_name,
            original=exc,
        ) from exc


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def rerank(
    query: str,
    chunks: list[str],
    top_k: int | None = None,
    model_name: str | None = None,
) -> list[str]:
    """
    Reordena os chunks candidatos por relevância em relação à query.

    Parâmetros
    ----------
    query:
        Pergunta do usuário.
    chunks:
        Lista de strings com o conteúdo de cada chunk candidato.
    top_k:
        Número de chunks a retornar após o reranking.
        Padrão: ``settings.RAG_TOP_K`` (4).
    model_name:
        Modelo CrossEncoder a usar.
        Padrão: ``settings.RAG_RERANKER_MODEL``.

    Retorno
    -------
    list[str]
        Lista com os ``top_k`` chunks mais relevantes, em ordem
        decrescente de score.

    Exceções
    --------
    RerankerError
        Se o sentence-transformers não estiver instalado ou ocorrer
        erro durante o scoring.
    """
    if not chunks:
        return []

    _top_k = top_k if top_k is not None else getattr(settings, "RAG_TOP_K", 4)
    _model_name = model_name or getattr(
        settings,
        "RAG_RERANKER_MODEL",
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
    )

    cross_encoder = _get_cross_encoder(_model_name)

    # Pares (query, chunk) para o CrossEncoder
    pairs = [(query, chunk) for chunk in chunks]

    try:
        scores: list[float] = cross_encoder.predict(pairs).tolist()
    except Exception as exc:
        raise RerankerError(
            f"Falha ao calcular scores com CrossEncoder '{_model_name}': {exc}",
            model_name=_model_name,
            original=exc,
        ) from exc

    # Ordena chunks por score decrescente e retorna os top_k
    ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)

    logger.debug(
        "Reranker: %d candidatos → top %d selecionados (scores: %s)",
        len(chunks),
        _top_k,
        [round(s, 4) for s, _ in ranked[:_top_k]],
    )

    return [chunk for _, chunk in ranked[:_top_k]]


def rerank_with_scores(
    query: str,
    chunks: list[str],
    top_k: int | None = None,
    model_name: str | None = None,
) -> list[tuple[float, str]]:
    """
    Igual a ``rerank``, mas retorna tuplas ``(score, chunk)`` para
    uso em debug ou avaliação de qualidade.
    """
    if not chunks:
        return []

    _top_k = top_k if top_k is not None else getattr(settings, "RAG_TOP_K", 4)
    _model_name = model_name or getattr(
        settings,
        "RAG_RERANKER_MODEL",
        "cross-encoder/ms-marco-MiniLM-L-6-v2",
    )

    cross_encoder = _get_cross_encoder(_model_name)
    pairs = [(query, chunk) for chunk in chunks]

    try:
        scores: list[float] = cross_encoder.predict(pairs).tolist()
    except Exception as exc:
        raise RerankerError(
            f"Falha ao calcular scores com CrossEncoder '{_model_name}': {exc}",
            model_name=_model_name,
            original=exc,
        ) from exc

    ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)
    return ranked[:_top_k]
