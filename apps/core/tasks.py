"""
Tasks Celery compartilhadas do core.

Tasks disponíveis:
    index_document(doc_id, doc_type)     — indexa um documento (gera chunks + embeddings)
    delete_document(doc_id, doc_type)    — remove chunks e o registro do documento
    reindex_document(doc_id, doc_type)   — delete + index encadeados via Celery chain

``doc_type`` aceita:
    "knowledge"  → KnowledgeDocument / KnowledgeChunk
    "personal"   → UserDocument / UserChunk

Pipeline de indexação:
    1. Busca o documento e seta status → "indexing"
    2. Extrai texto (PDF / DOCX / TXT / MD)
    3. Mascara PII/LGPD com Presidio (privacy_filter.mask)
    4. Chunkeia o texto mascarado (SemanticChunker ou fallback RecursiveCharacterTextSplitter)
    5. Gera embeddings em lote (sentence-transformers)
    6. Bulk insert dos chunks no pgvector
    7. Seta status → "ready" (ou "error" em caso de falha)
"""

from __future__ import annotations

import logging
from typing import Literal

from celery import chain, shared_task
from django.conf import settings

from apps.core.exceptions import (
    ChunkingError,
    DocumentProcessingError,
    EmbeddingError,
    TextExtractionError,
)
from apps.core.privacy_filter import mask as privacy_mask
from apps.core.rag_service import get_embeddings_batch
from apps.core.utils import extract_text

logger = logging.getLogger(__name__)

DocType = Literal["knowledge", "personal"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_document(doc_id: str, doc_type: DocType):
    """Retorna a instância do documento conforme o tipo."""
    if doc_type == "knowledge":
        from apps.knowledge.models import KnowledgeDocument
        return KnowledgeDocument.objects.get(pk=doc_id)
    elif doc_type == "personal":
        from apps.documents.models import UserDocument
        return UserDocument.objects.get(pk=doc_id)
    else:
        raise ValueError(f"doc_type inválido: '{doc_type}'. Use 'knowledge' ou 'personal'.")


def _get_chunk_model(doc_type: DocType):
    """Retorna o model de chunk conforme o tipo."""
    if doc_type == "knowledge":
        from apps.knowledge.models import KnowledgeChunk
        return KnowledgeChunk
    from apps.documents.models import UserChunk
    return UserChunk


def _chunk_text(text: str) -> list[str]:
    """
    Chunkeia o texto usando SemanticChunker (langchain-experimental).
    Fallback para RecursiveCharacterTextSplitter se o texto for muito curto
    ou se o SemanticChunker falhar.
    """
    breakpoint_type = getattr(settings, "RAG_SEMANTIC_BREAKPOINT", "percentile")
    embedding_model_name = getattr(settings, "EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    chunk_size = getattr(settings, "RAG_CHUNK_SIZE", 500)
    chunk_overlap = getattr(settings, "RAG_CHUNK_OVERLAP", 50)

    # Texto muito curto → pula SemanticChunker
    if len(text.split()) < 50:
        logger.debug("Texto curto (%d palavras), usando fallback direto.", len(text.split()))
        return _chunk_text_fallback(text, chunk_size, chunk_overlap)

    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings
        from langchain_experimental.text_splitter import SemanticChunker

        hf_embeddings = HuggingFaceEmbeddings(
            model_name=embedding_model_name,
            model_kwargs={"device": "cpu"},
        )
        chunker = SemanticChunker(
            embeddings=hf_embeddings,
            breakpoint_threshold_type=breakpoint_type,
        )
        chunks = chunker.split_text(text)
        logger.debug("SemanticChunker gerou %d chunks.", len(chunks))
        return [c for c in chunks if c.strip()]

    except Exception as exc:
        logger.warning(
            "SemanticChunker falhou (%s), usando fallback RecursiveCharacterTextSplitter.", exc
        )
        return _chunk_text_fallback(text, chunk_size, chunk_overlap)


def _chunk_text_fallback(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Fallback de chunking com RecursiveCharacterTextSplitter."""
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter
    except ImportError as exc:
        raise ChunkingError(
            "langchain não está instalado. Execute: uv add langchain",
            original=exc,
        ) from exc

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
    )
    chunks = splitter.split_text(text)
    logger.debug("Fallback splitter gerou %d chunks.", len(chunks))
    return [c for c in chunks if c.strip()]


def _build_knowledge_chunks(document, chunks: list[str], embeddings: list[list[float]]):
    """Monta instâncias KnowledgeChunk para bulk_create."""
    from apps.knowledge.models import KnowledgeChunk
    return [
        KnowledgeChunk(
            document=document,
            collection_id=document.collection_id,
            chunk_index=i,
            content=chunk,
            embedding=embedding,
        )
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]


def _build_personal_chunks(document, chunks: list[str], embeddings: list[list[float]]):
    """Monta instâncias UserChunk para bulk_create."""
    from apps.documents.models import UserChunk
    return [
        UserChunk(
            document=document,
            user_id=document.owner_id,
            chunk_index=i,
            content=chunk,
            embedding=embedding,
        )
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name="core.index_document",
)
def index_document(self, doc_id: str, doc_type: DocType) -> dict:
    """
    Indexa um documento: extrai texto → mascara PII → chunkeia → embeddings → pgvector.

    Parâmetros
    ----------
    doc_id:
        UUID do documento (str).
    doc_type:
        ``"knowledge"`` ou ``"personal"``.

    Retorno
    -------
    dict
        ``{"doc_id": ..., "doc_type": ..., "chunks_count": N, "status": "ready"}``
    """
    logger.info("[index_document] Iniciando doc_id=%s doc_type=%s", doc_id, doc_type)

    # 1. Busca o documento e atualiza status
    try:
        doc = _get_document(doc_id, doc_type)
    except Exception as exc:
        logger.error("[index_document] Documento não encontrado: %s", exc)
        raise

    doc.status = "indexing"
    doc.error_message = ""
    doc.save(update_fields=["status", "error_message"])

    try:
        # 2. Extrai texto
        logger.debug("[index_document] Extraindo texto de '%s'", doc.file_path if doc_type == "knowledge" else doc.file.path)
        file_path = doc.file_path if doc_type == "knowledge" else doc.file.path
        raw_text = extract_text(file_path, doc.file_type)

        if not raw_text.strip():
            raise DocumentProcessingError(
                f"Nenhum texto extraído do documento '{doc.title}'."
            )

        # 3. Máscara PII/LGPD
        logger.debug("[index_document] Aplicando filtro de privacidade…")
        masked_text, occurrences = privacy_mask(raw_text)

        if occurrences:
            logger.info(
                "[index_document] %d ocorrência(s) PII mascarada(s) em '%s'.",
                len(occurrences),
                doc.title,
            )

        # 4. Chunking
        logger.debug("[index_document] Chunkiando texto…")
        chunks = _chunk_text(masked_text)

        if not chunks:
            raise DocumentProcessingError(
                f"O chunking não gerou nenhum chunk para '{doc.title}'."
            )

        logger.info("[index_document] %d chunks gerados.", len(chunks))

        # 5. Embeddings em lote
        logger.debug("[index_document] Gerando embeddings (%d chunks)…", len(chunks))
        embeddings = get_embeddings_batch(chunks)

        # 6. Remove chunks antigos (re-indexação segura)
        ChunkModel = _get_chunk_model(doc_type)
        ChunkModel.objects.filter(document=doc).delete()

        # 7. Bulk insert
        if doc_type == "knowledge":
            chunk_objs = _build_knowledge_chunks(doc, chunks, embeddings)
        else:
            chunk_objs = _build_personal_chunks(doc, chunks, embeddings)

        ChunkModel.objects.bulk_create(chunk_objs, batch_size=100)
        logger.info("[index_document] %d chunks inseridos no pgvector.", len(chunk_objs))

        # 8. Atualiza documento como ready
        doc.status = "ready"
        doc.chunks_count = len(chunk_objs)
        doc.error_message = ""
        doc.save(update_fields=["status", "chunks_count", "error_message"])

        return {
            "doc_id": doc_id,
            "doc_type": doc_type,
            "chunks_count": len(chunk_objs),
            "status": "ready",
        }

    except (TextExtractionError, ChunkingError, EmbeddingError, DocumentProcessingError) as exc:
        logger.error("[index_document] Erro de processamento: %s", exc)
        doc.status = "error"
        doc.error_message = str(exc)
        doc.save(update_fields=["status", "error_message"])
        raise

    except Exception as exc:
        logger.exception("[index_document] Erro inesperado: %s", exc)
        doc.status = "error"
        doc.error_message = f"Erro inesperado: {exc}"
        doc.save(update_fields=["status", "error_message"])
        # Retry automático do Celery
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="core.delete_document",
)
def delete_document(self, doc_id: str, doc_type: DocType) -> dict:
    """
    Remove todos os chunks e o registro do documento do banco.

    Parâmetros
    ----------
    doc_id:
        UUID do documento (str).
    doc_type:
        ``"knowledge"`` ou ``"personal"``.

    Retorno
    -------
    dict
        ``{"doc_id": ..., "doc_type": ..., "status": "deleted"}``
    """
    logger.info("[delete_document] Iniciando doc_id=%s doc_type=%s", doc_id, doc_type)

    try:
        doc = _get_document(doc_id, doc_type)
    except Exception as exc:
        logger.warning("[delete_document] Documento não encontrado (já deletado?): %s", exc)
        return {"doc_id": doc_id, "doc_type": doc_type, "status": "not_found"}

    try:
        # Remove chunks do pgvector
        ChunkModel = _get_chunk_model(doc_type)
        deleted_count, _ = ChunkModel.objects.filter(document=doc).delete()
        logger.info("[delete_document] %d chunks removidos.", deleted_count)

        # Remove arquivo físico (somente documentos pessoais — FileField)
        if doc_type == "personal" and doc.file:
            try:
                doc.file.delete(save=False)
            except Exception as exc:
                logger.warning("[delete_document] Falha ao remover arquivo físico: %s", exc)

        # Remove o documento
        doc.delete()
        logger.info("[delete_document] Documento %s deletado com sucesso.", doc_id)

        return {"doc_id": doc_id, "doc_type": doc_type, "status": "deleted"}

    except Exception as exc:
        logger.exception("[delete_document] Erro inesperado: %s", exc)
        raise self.retry(exc=exc)


@shared_task(name="core.reindex_document")
def reindex_document(doc_id: str, doc_type: DocType) -> object:
    """
    Reindexação completa: delete → index encadeados via Celery chain.

    Parâmetros
    ----------
    doc_id:
        UUID do documento (str).
    doc_type:
        ``"knowledge"`` ou ``"personal"``.

    Retorno
    -------
    AsyncResult
        O resultado do chain (resultado do index_document).
    """
    logger.info("[reindex_document] Iniciando chain para doc_id=%s doc_type=%s", doc_id, doc_type)

    pipeline = chain(
        delete_document.si(doc_id, doc_type),
        index_document.si(doc_id, doc_type),
    )
    return pipeline.apply_async()
