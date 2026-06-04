"""
Modelos da app documents.

Hierarquia:
    UserDocument  ->  UserChunk

Integracao:
    - apps.core.tasks.index_document    -- preenche UserChunk (doc_type="personal")
    - apps.core.tasks.delete_document   -- remove UserChunk e arquivo fisico
    - apps.core.rag_service.RAGService  -- consulta UserChunk via l2_distance
"""

from __future__ import annotations

import uuid

from django.db import models
from pgvector.django import VectorField

from apps.core.models import TimeStampedModel


class UserDocument(TimeStampedModel):
    """
    Documento pessoal de um usuario.

    Ciclo de vida: pending -> indexing -> ready / error

    O arquivo e armazenado via FileField (MEDIA_ROOT/documents/<user_id>/).
    A indexacao e feita de forma assincrona pela task core.index_document
    com doc_type="personal".
    """

    class FileType(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "DOCX"
        TXT = "txt", "TXT"
        MD = "md", "Markdown"

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        INDEXING = "indexing", "Indexando"
        READY = "ready", "Pronto"
        ERROR = "error", "Erro"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False,
                          db_comment="UUID do documento pessoal.")
    owner = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.CASCADE, related_name="documents",
        verbose_name="proprietario", db_comment="Usuario dono do documento.",
    )
    title = models.CharField(
        "titulo", max_length=300,
        help_text="Titulo descritivo do documento exibido nas respostas do RAG como fonte.",
        db_comment="Titulo descritivo do documento pessoal.",
    )
    file = models.FileField(
        "arquivo", upload_to="documents/%Y/%m/",
        help_text="Arquivo do documento (pdf, docx, txt, md). Armazenado em MEDIA_ROOT.",
        db_comment="Caminho relativo do arquivo dentro do MEDIA_ROOT.",
    )
    file_type = models.CharField(
        "tipo de arquivo", max_length=10, choices=FileType.choices,
        help_text="Formato do arquivo para extracao de texto.",
        db_comment="Formato do arquivo (pdf, docx, txt, md).",
    )
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.PENDING,
        db_index=True, help_text="Estado atual do processamento do documento.",
        db_comment="Estado de processamento: pending -> indexing -> ready/error.",
    )
    chunks_count = models.IntegerField(
        "numero de chunks", default=0,
        help_text="Quantidade de chunks gerados apos indexacao bem-sucedida.",
        db_comment="Total de chunks indexados no pgvector.",
    )
    error_message = models.TextField(
        "mensagem de erro", blank=True, default="",
        help_text="Detalhes do erro ocorrido durante a indexacao.",
        db_comment="Mensagem de erro da ultima tentativa falha.",
    )

    class Meta:
        verbose_name = "documento pessoal"
        verbose_name_plural = "documentos pessoais"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "status"], name="user_doc_owner_status_idx"),
        ]
        db_table_comment = (
            "Documentos pessoais dos usuarios indexados no pipeline RAG. "
            "Cada documento origina N UserChunk com embedding pgvector. "
            "Visivel apenas ao proprietario."
        )

    def __str__(self) -> str:
        return f"{self.title} [{self.get_status_display()}]"

    @property
    def is_ready(self) -> bool:
        return self.status == self.Status.READY

    def trigger_indexing(self) -> str:
        from apps.core.tasks import index_document
        result = index_document.delay(str(self.id), "personal")
        return result.id

    def trigger_reindex(self) -> str:
        from apps.core.tasks import reindex_document
        result = reindex_document.delay(str(self.id), "personal")
        return result.id


class UserChunk(models.Model):
    """
    Trecho (chunk) de um UserDocument com embedding pgvector.

    Gerado automaticamente por apps.core.tasks.index_document (doc_type="personal").
    Nao deve ser criado/editado manualmente.

    O campo user_id e desnormalizado para permitir filtros eficientes no pgvector
    sem JOIN com UserDocument.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False,
                          db_comment="UUID do chunk pessoal.")
    document = models.ForeignKey(
        UserDocument, on_delete=models.CASCADE, related_name="chunks",
        verbose_name="documento", db_comment="Documento do qual este chunk foi extraido.",
    )
    user_id = models.UUIDField(
        "ID do usuario", db_index=True,
        help_text="UUID do proprietario (desnormalizado para buscas eficientes no pgvector).",
        db_comment=(
            "UUID desnormalizado do CustomUser para filtrar chunks "
            "por usuario sem JOIN no momento da busca vetorial."
        ),
    )
    chunk_index = models.IntegerField(
        "indice do chunk",
        help_text="Posicao do chunk no documento original (0-based).",
        db_comment="Ordem do chunk dentro do documento (0-based).",
    )
    content = models.TextField(
        "conteudo",
        help_text="Texto do chunk apos extracao e mascaramento PII/LGPD.",
        db_comment="Texto do chunk pos-mascaramento Presidio.",
    )
    embedding = VectorField(
        "embedding", dimensions=384,
        help_text="Vetor de 384 dimensoes gerado pelo modelo all-MiniLM-L6-v2.",
        db_comment="Embedding pgvector (384d, all-MiniLM-L6-v2) para busca L2.",
    )

    class Meta:
        verbose_name = "chunk pessoal"
        verbose_name_plural = "chunks pessoais"
        ordering = ["document", "chunk_index"]
        unique_together = [("document", "chunk_index")]
        indexes = [
            models.Index(fields=["user_id"], name="user_chunk_user_idx"),
        ]
        db_table_comment = (
            "Chunks indexados de documentos pessoais com embeddings pgvector. "
            "Consultado pelo RAGService via l2_distance para busca semantica "
            "quando use_personal_docs=True na conversa."
        )

    def __str__(self) -> str:
        return f"Chunk {self.chunk_index} -- {self.document.title}"
