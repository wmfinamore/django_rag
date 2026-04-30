"""
Utilitários compartilhados do projeto django_rag.

Funções:
    extract_text(file_path, file_type) → str
        Extrai o texto bruto de arquivos PDF, DOCX, TXT e MD.
        Levanta TextExtractionError em caso de falha.
"""

from __future__ import annotations

import logging
from pathlib import Path

from apps.core.exceptions import TextExtractionError

logger = logging.getLogger(__name__)

# Tipos de arquivo suportados
SUPPORTED_FILE_TYPES = {"pdf", "docx", "txt", "md"}


def extract_text(file_path: str | Path, file_type: str) -> str:
    """
    Extrai texto bruto de um arquivo.

    Parâmetros
    ----------
    file_path:
        Caminho absoluto ou relativo ao arquivo.
    file_type:
        Extensão sem ponto: ``"pdf"``, ``"docx"``, ``"txt"`` ou ``"md"``.

    Retorno
    -------
    str
        Texto extraído, sem normalização adicional.

    Exceções
    --------
    TextExtractionError
        Se o tipo de arquivo não for suportado ou ocorrer erro na extração.
    """
    file_type = file_type.lower().lstrip(".")
    path = Path(file_path)

    if file_type not in SUPPORTED_FILE_TYPES:
        raise TextExtractionError(
            f"Tipo de arquivo não suportado: '{file_type}'. "
            f"Suportados: {', '.join(sorted(SUPPORTED_FILE_TYPES))}",
            file_path=str(path),
        )

    if not path.exists():
        raise TextExtractionError(
            f"Arquivo não encontrado: {path}",
            file_path=str(path),
        )

    logger.debug("Extraindo texto de '%s' (tipo: %s)", path, file_type)

    try:
        if file_type == "pdf":
            return _extract_pdf(path)
        if file_type == "docx":
            return _extract_docx(path)
        if file_type in {"txt", "md"}:
            return _extract_plain(path)
    except TextExtractionError:
        raise
    except Exception as exc:
        raise TextExtractionError(
            f"Erro inesperado ao extrair texto de '{path}': {exc}",
            file_path=str(path),
            original=exc,
        ) from exc

    # Nunca atingido, mas satisfaz o type-checker
    return ""  # pragma: no cover


# ---------------------------------------------------------------------------
# Extratores internos
# ---------------------------------------------------------------------------


def _extract_pdf(path: Path) -> str:
    """Extrai texto de PDF usando pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise TextExtractionError(
            "pypdf não está instalado. Execute: uv add pypdf",
            file_path=str(path),
            original=exc,
        ) from exc

    reader = PdfReader(str(path))
    parts: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
            parts.append(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao extrair página %d de '%s': %s", i, path, exc)

    return "\n".join(parts)


def _extract_docx(path: Path) -> str:
    """Extrai texto de DOCX usando python-docx."""
    try:
        import docx  # python-docx
    except ImportError as exc:
        raise TextExtractionError(
            "python-docx não está instalado. Execute: uv add python-docx",
            file_path=str(path),
            original=exc,
        ) from exc

    doc = docx.Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def _extract_plain(path: Path) -> str:
    """Lê arquivos de texto puro (TXT, MD) com detecção de encoding."""
    # Tenta UTF-8 primeiro; cai para latin-1 como fallback seguro
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue

    raise TextExtractionError(
        f"Não foi possível detectar o encoding de '{path}'.",
        file_path=str(path),
    )


# ---------------------------------------------------------------------------
# Utilitários de texto
# ---------------------------------------------------------------------------


def truncate_text(text: str, max_chars: int = 500) -> str:
    """Trunca o texto para exibição em logs/admin, preservando palavras."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(" ", 1)[0]
    return truncated + "…"


def normalize_whitespace(text: str) -> str:
    """Remove espaços/quebras de linha redundantes sem perder estrutura."""
    import re
    # Colapsa múltiplas linhas em branco para uma única linha em branco
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Colapsa múltiplos espaços em um único (mantém quebras de linha)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()
