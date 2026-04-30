"""
Exceções customizadas do projeto django_rag.

Hierarquia:
    DjangoRAGError                    — base de todas as exceções do projeto
    ├── DocumentProcessingError       — falha ao processar/indexar documento
    │   ├── TextExtractionError       — falha ao extrair texto do arquivo
    │   └── ChunkingError             — falha ao chunkar o texto extraído
    ├── EmbeddingError                — falha ao gerar embeddings
    ├── RerankerError                 — falha no reranker CrossEncoder
    ├── RAGError                      — falha no pipeline RAG em geral
    │   └── LLMError                  — falha na chamada ao Ollama/LLM
    └── PrivacyFilterError            — falha no filtro PII/LGPD (Presidio)
"""


class DjangoRAGError(Exception):
    """Base de todas as exceções do projeto django_rag."""


# ---------------------------------------------------------------------------
# Processamento de documentos
# ---------------------------------------------------------------------------


class DocumentProcessingError(DjangoRAGError):
    """Falha genérica ao processar ou indexar um documento."""


class TextExtractionError(DocumentProcessingError):
    """Falha ao extrair texto de um arquivo (PDF, DOCX, TXT, MD).

    Atributos:
        file_path: caminho do arquivo que gerou o erro.
        original: exceção original que causou a falha.
    """

    def __init__(self, message: str, file_path: str = "", original: Exception | None = None):
        super().__init__(message)
        self.file_path = file_path
        self.original = original


class ChunkingError(DocumentProcessingError):
    """Falha ao chunkar o texto extraído (SemanticChunker ou fallback).

    Atributos:
        original: exceção original que causou a falha.
    """

    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message)
        self.original = original


# ---------------------------------------------------------------------------
# Embeddings e reranking
# ---------------------------------------------------------------------------


class EmbeddingError(DjangoRAGError):
    """Falha ao gerar embeddings com sentence-transformers.

    Atributos:
        model_name: nome do modelo de embedding que falhou.
        original: exceção original que causou a falha.
    """

    def __init__(self, message: str, model_name: str = "", original: Exception | None = None):
        super().__init__(message)
        self.model_name = model_name
        self.original = original


class RerankerError(DjangoRAGError):
    """Falha no reranker CrossEncoder (ms-marco-MiniLM-L-6-v2).

    Atributos:
        model_name: nome do modelo de reranking que falhou.
        original: exceção original que causou a falha.
    """

    def __init__(self, message: str, model_name: str = "", original: Exception | None = None):
        super().__init__(message)
        self.model_name = model_name
        self.original = original


# ---------------------------------------------------------------------------
# Pipeline RAG / LLM
# ---------------------------------------------------------------------------


class RAGError(DjangoRAGError):
    """Falha genérica no pipeline RAG (retrieval, prompt building, etc.)."""


class LLMError(RAGError):
    """Falha na chamada ao LLM (Ollama).

    Atributos:
        model_name: modelo que gerou o erro.
        status_code: HTTP status code retornado pelo Ollama, se disponível.
        original: exceção original que causou a falha.
    """

    def __init__(
        self,
        message: str,
        model_name: str = "",
        status_code: int | None = None,
        original: Exception | None = None,
    ):
        super().__init__(message)
        self.model_name = model_name
        self.status_code = status_code
        self.original = original


# ---------------------------------------------------------------------------
# Filtro de privacidade
# ---------------------------------------------------------------------------


class PrivacyFilterError(DjangoRAGError):
    """Falha no filtro PII/LGPD (Presidio).

    Atributos:
        original: exceção original que causou a falha.
    """

    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message)
        self.original = original
