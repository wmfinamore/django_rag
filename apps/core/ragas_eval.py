"""
Avaliação do pipeline RAG com Ragas + Ollama local.

Métricas coletadas:
    - Faithfulness      — resposta fundamentada nos chunks recuperados?
    - Answer Relevancy  — resposta relevante para a pergunta?
    - Context Recall    — chunks cobrem a resposta correta?
    - Context Precision — chunks são precisos (sem ruído)?

Uso programático::

    from apps.core.ragas_eval import evaluate_pipeline

    dataset = [
        {
            "question":     "Quantos dias de férias o colaborador tem direito?",
            "answer":       "O colaborador tem direito a 30 dias de férias.",
            "contexts":     ["...trecho do documento...", "...outro trecho..."],
            "ground_truth": "30 dias corridos por ano de trabalho.",
        },
        ...
    ]
    scores = evaluate_pipeline(dataset)
    # scores: {"faithfulness": 0.87, "answer_relevancy": 0.91, ...}

Via management command::

    python manage.py eval_rag --collection politicas-rh --samples 20
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Construção dos wrappers Ragas
# ---------------------------------------------------------------------------


def _build_ragas_llm():
    """Instancia o LLM wrapper para o Ragas via langchain-ollama."""
    try:
        from langchain_ollama import OllamaLLM
        from ragas.llms import LangchainLLMWrapper
    except ImportError as exc:
        raise ImportError(
            "ragas e/ou langchain-ollama não estão instalados. "
            "Execute: uv add ragas langchain-ollama",
        ) from exc

    base_url = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    model = getattr(settings, "OLLAMA_LLM_MODEL", "llama3.2:3b")
    temperature = getattr(settings, "OLLAMA_TEMPERATURE", 0.3)

    llm = OllamaLLM(base_url=base_url, model=model, temperature=temperature)
    return LangchainLLMWrapper(llm)


def _build_ragas_embeddings():
    """Instancia o wrapper de embeddings para o Ragas via HuggingFace."""
    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings
        from ragas.embeddings import LangchainEmbeddingsWrapper
    except ImportError as exc:
        raise ImportError(
            "ragas e/ou langchain-community não estão instalados. "
            "Execute: uv add ragas langchain-community",
        ) from exc

    model_name = getattr(settings, "EMBEDDING_MODEL", "all-MiniLM-L6-v2")

    embeddings = HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": "cpu"},
    )
    return LangchainEmbeddingsWrapper(embeddings)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def evaluate_pipeline(dataset: list[dict]) -> dict[str, float]:
    """
    Avalia o pipeline RAG com as 4 métricas padrão do Ragas.

    Parâmetros
    ----------
    dataset:
        Lista de dicts com as chaves obrigatórias:
            - ``question``    (str)          — pergunta do usuário
            - ``answer``      (str)          — resposta gerada pelo LLM
            - ``contexts``    (list[str])    — chunks usados no prompt
            - ``ground_truth``(str)          — resposta esperada (para context_recall)

    Retorno
    -------
    dict[str, float]
        Scores médios para cada métrica:
        ``{"faithfulness": ..., "answer_relevancy": ...,
           "context_recall": ..., "context_precision": ...}``

    Exceções
    --------
    ImportError
        Se ragas, langchain-ollama ou langchain-community não estiverem instalados.
    ValueError
        Se o dataset estiver vazio ou com campos faltando.
    """
    if not dataset:
        raise ValueError("O dataset de avaliação está vazio.")

    _validate_dataset(dataset)

    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import (
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )
    except ImportError as exc:
        raise ImportError(
            "ragas e/ou datasets não estão instalados. "
            "Execute: uv add ragas datasets",
        ) from exc

    ragas_llm = _build_ragas_llm()
    ragas_embeddings = _build_ragas_embeddings()

    metrics = [faithfulness, answer_relevancy, context_recall, context_precision]

    hf_dataset = Dataset.from_list(dataset)

    logger.info("Iniciando avaliação Ragas com %d amostras…", len(dataset))

    result = evaluate(
        dataset=hf_dataset,
        metrics=metrics,
        llm=ragas_llm,
        embeddings=ragas_embeddings,
    )

    scores = {
        "faithfulness": round(float(result["faithfulness"]), 4),
        "answer_relevancy": round(float(result["answer_relevancy"]), 4),
        "context_recall": round(float(result["context_recall"]), 4),
        "context_precision": round(float(result["context_precision"]), 4),
    }

    logger.info("Avaliação concluída: %s", scores)
    return scores


# ---------------------------------------------------------------------------
# Validação do dataset
# ---------------------------------------------------------------------------


def _validate_dataset(dataset: list[dict]) -> None:
    """Valida que cada amostra possui os campos obrigatórios."""
    required = {"question", "answer", "contexts", "ground_truth"}
    for i, sample in enumerate(dataset):
        missing = required - set(sample.keys())
        if missing:
            raise ValueError(
                f"Amostra #{i} está faltando os campos: {', '.join(sorted(missing))}. "
                f"Campos obrigatórios: {', '.join(sorted(required))}."
            )
        if not isinstance(sample.get("contexts"), list):
            raise ValueError(
                f"Amostra #{i}: 'contexts' deve ser uma lista de strings."
            )
