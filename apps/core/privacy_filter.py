"""
Filtro de privacidade PII/LGPD usando Microsoft Presidio.

Detecta e mascara dados sensíveis no texto extraído dos documentos
ANTES do chunking e da geração de embeddings. Nenhum dado sensível
chega ao pgvector.

Entidades detectadas e seus placeholders:

    BR_CPF          → [CPF]
    BR_CNPJ         → [CNPJ]
    BR_RG           → [RG]
    EMAIL_ADDRESS   → [EMAIL]
    PHONE_NUMBER    → [TELEFONE]
    LOCATION        → [ENDERECO]
    CREDIT_CARD     → [CARTAO]
    IBAN_CODE       → [CONTA_BANCARIA]
    PERSON          → [PESSOA]

Uso::

    from apps.core.privacy_filter import mask

    texto_mascarado, ocorrencias = mask(texto_bruto)
    # ocorrencias: [{"type": "BR_CPF", "score": 0.95, "start": 10, "end": 21}, ...]

Configuração (settings):
    PRIVACY_MIN_SCORE  (float, padrão 0.7)  — score mínimo de confiança
    PRIVACY_LANGUAGE   (str,   padrão "pt") — idioma do analisador
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from django.conf import settings

from apps.core.exceptions import PrivacyFilterError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapa entidade → placeholder
# ---------------------------------------------------------------------------

ENTITY_PLACEHOLDERS: dict[str, str] = {
    "BR_CPF": "[CPF]",
    "BR_CNPJ": "[CNPJ]",
    "BR_RG": "[RG]",
    "EMAIL_ADDRESS": "[EMAIL]",
    "PHONE_NUMBER": "[TELEFONE]",
    "LOCATION": "[ENDERECO]",
    "CREDIT_CARD": "[CARTAO]",
    "IBAN_CODE": "[CONTA_BANCARIA]",
    "PERSON": "[PESSOA]",
}

ENTITIES: list[str] = list(ENTITY_PLACEHOLDERS.keys())


# ---------------------------------------------------------------------------
# Singleton dos engines Presidio (carregados uma vez por processo)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _get_engines():
    """
    Carrega AnalyzerEngine e AnonymizerEngine do Presidio.
    O ``@lru_cache`` garante que o modelo spaCy seja carregado apenas
    uma vez por processo worker (Celery ou Django).

    Levanta PrivacyFilterError se o Presidio não estiver instalado.
    """
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine
    except ImportError as exc:
        raise PrivacyFilterError(
            "presidio-analyzer / presidio-anonymizer não estão instalados. "
            "Execute: uv add presidio-analyzer presidio-anonymizer",
            original=exc,
        ) from exc

    logger.info("Carregando Presidio AnalyzerEngine (spaCy pt_core_news_lg)…")
    try:
        analyzer = AnalyzerEngine()
    except Exception as exc:
        raise PrivacyFilterError(
            "Falha ao inicializar o AnalyzerEngine do Presidio. "
            "Verifique se o modelo spaCy está instalado: "
            "uv run python -m spacy download pt_core_news_lg",
            original=exc,
        ) from exc

    anonymizer = AnonymizerEngine()
    logger.info("Presidio pronto.")
    return analyzer, anonymizer


def _build_operators() -> dict:
    """Constrói o dict de OperatorConfig para o AnonymizerEngine."""
    from presidio_anonymizer.entities import OperatorConfig

    return {
        entity: OperatorConfig("replace", {"new_value": placeholder})
        for entity, placeholder in ENTITY_PLACEHOLDERS.items()
    }


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def mask(
    text: str,
    language: str | None = None,
    min_score: float | None = None,
) -> tuple[str, list[dict]]:
    """
    Mascara dados sensíveis no texto.

    Parâmetros
    ----------
    text:
        Texto bruto extraído do documento.
    language:
        Idioma do analisador (padrão: ``settings.PRIVACY_LANGUAGE`` ou ``"pt"``).
    min_score:
        Score mínimo de confiança para considerar uma detecção
        (padrão: ``settings.PRIVACY_MIN_SCORE`` ou ``0.7``).

    Retorno
    -------
    tuple[str, list[dict]]
        - texto_mascarado: texto com os placeholders no lugar dos dados sensíveis.
        - ocorrencias: lista de dicts com ``type``, ``score``, ``start``, ``end``
          de cada entidade detectada (para log de auditoria).

    Exceções
    --------
    PrivacyFilterError
        Se o Presidio não estiver instalado ou falhar durante a análise.
    """
    if not text or not text.strip():
        return text, []

    _language = language or getattr(settings, "PRIVACY_LANGUAGE", "pt")
    _min_score = min_score if min_score is not None else getattr(settings, "PRIVACY_MIN_SCORE", 0.7)

    try:
        analyzer, anonymizer = _get_engines()
        operators = _build_operators()
    except PrivacyFilterError:
        raise
    except Exception as exc:
        raise PrivacyFilterError(
            f"Erro ao inicializar os engines do Presidio: {exc}",
            original=exc,
        ) from exc

    try:
        results = analyzer.analyze(
            text=text,
            language=_language,
            entities=ENTITIES,
            score_threshold=_min_score,
        )
    except Exception as exc:
        raise PrivacyFilterError(
            f"Falha ao analisar o texto com Presidio: {exc}",
            original=exc,
        ) from exc

    try:
        anonymized = anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators=operators,
        )
    except Exception as exc:
        raise PrivacyFilterError(
            f"Falha ao anonimizar o texto com Presidio: {exc}",
            original=exc,
        ) from exc

    occurrences = [
        {
            "type": r.entity_type,
            "score": round(r.score, 4),
            "start": r.start,
            "end": r.end,
        }
        for r in results
    ]

    if occurrences:
        logger.info(
            "Presidio mascarou %d ocorrência(s): %s",
            len(occurrences),
            ", ".join(o["type"] for o in occurrences),
        )

    return anonymized.text, occurrences
