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
# Reconhecedores customizados para entidades brasileiras
# ---------------------------------------------------------------------------

def _build_br_recognizers() -> list:
    """
    Cria reconhecedores de padrão para entidades brasileiras que o Presidio
    não inclui por padrão para o idioma 'pt':

    - BR_CPF    : NNN.NNN.NNN-NN  ou  NNNNNNNNNNN
    - BR_CNPJ   : NN.NNN.NNN/NNNN-NN  ou  NNNNNNNNNNNNNN
    - BR_RG     : N.NNN.NNN-N / N.NNN.NNN  (formato SP e variações)
    - CREDIT_CARD: padrões internacionais, language="pt"
    """
    from presidio_analyzer import Pattern, PatternRecognizer

    # --- CPF ---
    cpf_recognizer = PatternRecognizer(
        supported_entity="BR_CPF",
        supported_language="pt",
        patterns=[
            Pattern(
                name="cpf_formatted",
                regex=r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",
                score=0.85,
            ),
            Pattern(
                name="cpf_unformatted",
                regex=r"\b\d{11}\b",
                score=0.5,
            ),
        ],
        context=["cpf", "cadastro de pessoa", "documento"],
    )

    # --- CNPJ ---
    cnpj_recognizer = PatternRecognizer(
        supported_entity="BR_CNPJ",
        supported_language="pt",
        patterns=[
            Pattern(
                name="cnpj_formatted",
                regex=r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b",
                score=0.85,
            ),
            Pattern(
                name="cnpj_unformatted",
                regex=r"\b\d{14}\b",
                score=0.5,
            ),
        ],
        context=["cnpj", "empresa", "razao social"],
    )

    # --- RG ---
    rg_recognizer = PatternRecognizer(
        supported_entity="BR_RG",
        supported_language="pt",
        patterns=[
            Pattern(
                name="rg_formatted",
                regex=r"\b\d{1,2}\.\d{3}\.\d{3}-[\dxX]\b",
                score=0.75,
            ),
            Pattern(
                name="rg_plain",
                regex=r"\b\d{7,9}\b",
                score=0.4,
            ),
        ],
        context=["rg", "registro geral", "identidade", "carteira de identidade"],
    )

    # --- Cartão de crédito (pt) ---
    # O CreditCardRecognizer nativo só suporta 'en'; criamos um equivalente para 'pt'.
    credit_card_recognizer = PatternRecognizer(
        supported_entity="CREDIT_CARD",
        supported_language="pt",
        patterns=[
            Pattern(
                name="credit_card_with_spaces",
                regex=r"\b(?:4[0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}|"
                      r"5[1-5][0-9]{2}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}|"
                      r"3[47][0-9]{2}[ -]?[0-9]{6}[ -]?[0-9]{5}|"
                      r"6(?:011|5[0-9]{2})[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4})\b",
                score=0.85,
            ),
            Pattern(
                name="credit_card_plain",
                regex=r"\b(?:4[0-9]{15}|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b",
                score=0.6,
            ),
        ],
        context=["cartao", "cartão", "credito", "crédito", "visa", "mastercard", "amex"],
    )

    return [cpf_recognizer, cnpj_recognizer, rg_recognizer, credit_card_recognizer]


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
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        # Configura explicitamente o modelo português para evitar que o
        # Presidio carregue o default inglês (en_core_web_lg) e rejeite
        # chamadas com language="pt".
        nlp_configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "pt", "model_name": "pt_core_news_lg"}],
        }
        nlp_engine = NlpEngineProvider(nlp_configuration=nlp_configuration).create_engine()
        analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["pt"])
    except Exception as exc:
        raise PrivacyFilterError(
            "Falha ao inicializar o AnalyzerEngine do Presidio. "
            "Verifique se o modelo spaCy está instalado: "
            "uv run python -m spacy download pt_core_news_lg",
            original=exc,
        ) from exc

    # Registra reconhecedores brasileiros que o Presidio não inclui para 'pt'
    for recognizer in _build_br_recognizers():
        analyzer.registry.add_recognizer(recognizer)
        logger.debug("Reconhecedor registrado: %s (pt)", recognizer.supported_entities)

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
