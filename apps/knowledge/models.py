"""
Modelos da app knowledge — base de conhecimento institucional.

Hierarquia:
    KnowledgeCollection  →  KnowledgeDocument  →  KnowledgeChunk

- KnowledgeCollection: agrupa documentos por tema/área e controla acesso via grupos Django.
- KnowledgeDocument:   representa um arquivo (PDF, DOCX, TXT, MD) carregado em uma coleção.
- KnowledgeChunk:      trecho indexado de um documento com embedding pgvector para busca semântica.

Integração:
    - apps.core.tasks.index_document  — preenche KnowledgeChunk a partir de KnowledgeDocument
    - apps.core.rag_service.RAGService — consulta KnowledgeChunk via l2_distance
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import Group
from django.db import models
from pgvector.django import VectorField

from apps.core.models import TimeStampedModel


# ---------------------------------------------------------------------------
# KnowledgeCollection
# ---------------------------------------------------------------------------


class KnowledgeCollection(TimeStampedModel):
    """
    Coleção de documentos institucionais.

    Controle de acesso:
        Apenas usuários cujos grupos Django estejam em ``allowed_groups``
        podem ler/usar esta coleção nas queries RAG.
        Quando ``allowed_groups`` está vazio, a coleção é considerada pública
        (acessível a qualquer usuário autenticado).

    Campos:
        id            — UUID PK gerado automaticamente.
        name          — Nome único da coleção (ex: "RH – Políticas Internas").
        description   — Descrição opcional do conteúdo da coleção.
        allowed_groups— Grupos Django com acesso. Vazio = público.
        is_active     — Coleções inativas são ignoradas pelo RAGService.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="UUID da coleção gerado automaticamente.",
    )
    name = models.CharField(
        "nome",
        max_length=200,
        unique=True,
        help_text="Nome único da coleção (ex: 'RH – Políticas Internas').",
        db_comment="Nome único da coleção institucional.",
    )
    description = models.TextField(
        "descrição",
        blank=True,
        default="",
        help_text="Descrição do conteúdo e finalidade da coleção.",
        db_comment="Texto livre descrevendo o conteúdo da coleção.",
    )
    allowed_groups = models.ManyToManyField(
        Group,
        verbose_name="grupos com acesso",
        blank=True,
        related_name="knowledge_collections",
        help_text=(
            "Grupos Django que podem acessar esta coleção. "
            "Deixe em branco para acesso público a qualquer usuário autenticado."
        ),
    )
    is_active = models.BooleanField(
        "ativa",
        default=True,
        help_text="Coleções inativas são ignoradas pelo pipeline RAG.",
        db_comment="Flag de ativação; FALSE exclui a coleção das buscas RAG.",
    )

    class Meta:
        verbose_name = "coleção de conhecimento"
        verbose_name_plural = "coleções de conhecimento"
        ordering = ["name"]
        db_table_comment = (
            "Coleções de documentos institucionais. Controla acesso via "
            "grupos Django e agrupa KnowledgeDocument para fins de RAG."
        )

    def __str__(self) -> str:
        return self.name

    def is_accessible_by(self, user) -> bool:
        """
        Verifica se ``user`` tem acesso à coleção.

        Regras:
            - Superusuários sempre têm acesso.
            - Se ``allowed_groups`` estiver vazio, acesso público (qualquer autenticado).
            - Caso contrário, o usuário precisa pertencer a ao menos um grupo permitido.
        """
        if not self.is_active:
            return False
        if user.is_superuser:
            return True
        allowed = self.allowed_groups.all()
        if not allowed.exists():
            return True  # acesso público
        return user.groups.filter(pk__in=allowed).exists()


# ---------------------------------------------------------------------------
# KnowledgeDocument
# ---------------------------------------------------------------------------


class KnowledgeDocument(TimeStampedModel):
    """
    Documento institucional associado a uma KnowledgeCollection.

    Ciclo de vida:
        pending  → indexing  → ready
                             → error

    O campo ``file_path`` deve apontar para um caminho acessível pelo worker
    Celery que executará ``apps.core.tasks.index_document``.

    Campos:
        id            — UUID PK.
        collection    — Coleção à qual o documento pertence (cascade delete).
        title         — Título descritivo do documento.
        file_path     — Caminho absoluto do arquivo no filesystem.
        file_type     — Formato do arquivo (pdf, docx, txt, md).
        status        — Estado de processamento (pending/indexing/ready/error).
        chunks_count  — Número de chunks gerados após indexação bem-sucedida.
        error_message — Mensagem de erro da última tentativa de indexação.
        ingested_by   — Usuário que enviou o documento (SET_NULL se deletado).
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

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="UUID do documento gerado automaticamente.",
    )
    collection = models.ForeignKey(
        KnowledgeCollection,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="coleção",
        db_comment="Coleção à qual este documento pertence.",
    )
    title = models.CharField(
        "título",
        max_length=300,
        help_text="Título descritivo exibido nas respostas do RAG como fonte.",
        db_comment="Título descritivo do documento usado como fonte no RAG.",
    )
    file_path = models.CharField(
        "caminho do arquivo",
        max_length=1000,
        help_text="Caminho absoluto do arquivo no filesystem (acessível pelo worker Celery).",
        db_comment="Caminho absoluto do arquivo no servidor.",
    )
    file_type = models.CharField(
        "tipo de arquivo",
        max_length=10,
        choices=FileType.choices,
        help_text="Formato do arquivo para extração de texto.",
        db_comment="Formato do arquivo (pdf, docx, txt, md).",
    )
    status = models.CharField(
        "status",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        help_text="Estado atual do processamento do documento.",
        db_comment="Estado de processamento: pending → indexing → ready/error.",
    )
    chunks_count = models.IntegerField(
        "número de chunks",
        default=0,
        help_text="Quantidade de chunks gerados após indexação bem-sucedida.",
        db_comment="Total de chunks indexados no pgvector para este documento.",
    )
    error_message = models.TextField(
        "mensagem de erro",
        blank=True,
        default="",
        help_text="Detalhes do erro ocorrido durante a indexação (quando status=error).",
        db_comment="Mensagem de erro da última tentativa de indexação falha.",
    )
    ingested_by = models.ForeignKey(
        "accounts.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ingested_documents",
        verbose_name="ingerido por",
        db_comment="Usuário que realizou o upload/ingestão do documento.",
    )

    class Meta:
        verbose_name = "documento de conhecimento"
        verbose_name_plural = "documentos de conhecimento"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["collection", "status"], name="knowledge_doc_coll_status_idx"),
        ]
        db_table_comment = (
            "Documentos institucionais indexados no pipeline RAG. "
            "Cada documento origina N KnowledgeChunk com embedding pgvector."
        )

    def __str__(self) -> str:
        return f"{self.title} [{self.get_status_display()}]"

    @property
    def is_ready(self) -> bool:
        """Retorna True se o documento foi indexado com sucesso."""
        return self.status == self.Status.READY

    def trigger_indexing(self) -> str:
        """
        Enfileira a task Celery de indexação e retorna o task_id.

        Uso::

            task_id = document.trigger_indexing()
        """
        from apps.core.tasks import index_document

        result = index_document.delay(str(self.id), "knowledge")
        return result.id

    def trigger_reindex(self) -> str:
        """
        Enfileira a task Celery de re-indexação (delete + index) e retorna o task_id.
        """
        from apps.core.tasks import reindex_document

        result = reindex_document.delay(str(self.id), "knowledge")
        return result.id


# ---------------------------------------------------------------------------
# KnowledgeChunk
# ---------------------------------------------------------------------------


class KnowledgeChunk(models.Model):
    """
    Trecho (chunk) de um KnowledgeDocument com embedding pgvector.

    Gerado automaticamente pela task ``apps.core.tasks.index_document``.
    Não deve ser criado/editado manualmente.

    Campos:
        id              — UUID PK.
        document        — Documento de origem (cascade delete).
        collection_id   — UUID da coleção (desnormalizado para filtros eficientes no pgvector).
        chunk_index     — Posição do chunk no documento (0-based).
        content         — Texto do chunk (pós-mascaramento PII).
        embedding       — Vetor de 384 dimensões (all-MiniLM-L6-v2).
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        db_comment="UUID do chunk gerado automaticamente.",
    )
    document = models.ForeignKey(
        KnowledgeDocument,
        on_delete=models.CASCADE,
        related_name="chunks",
        verbose_name="documento",
        db_comment="Documento do qual este chunk foi extraído.",
    )
    collection_id = models.UUIDField(
        "ID da coleção",
        db_index=True,
        help_text="UUID da coleção (desnormalizado para buscas eficientes no pgvector).",
        db_comment=(
            "UUID desnormalizado da KnowledgeCollection para filtrar chunks "
            "por coleção sem JOIN no momento da busca vetorial."
        ),
    )
    chunk_index = models.IntegerField(
        "índice do chunk",
        help_text="Posição do chunk no documento original (0-based).",
        db_comment="Ordem do chunk dentro do documento (0-based).",
    )
    content = models.TextField(
        "conteúdo",
        help_text="Texto do chunk após extração e mascaramento PII/LGPD.",
        db_comment="Texto do chunk pós-mascaramento Presidio.",
    )
    embedding = VectorField(
        "embedding",
        dimensions=384,
        help_text="Vetor de 384 dimensões gerado pelo modelo all-MiniLM-L6-v2.",
        db_comment="Embedding pgvector (384d, all-MiniLM-L6-v2) para busca semântica L2.",
    )

    class Meta:
        verbose_name = "chunk de conhecimento"
        verbose_name_plural = "chunks de conhecimento"
        ordering = ["document", "chunk_index"]
        unique_together = [("document", "chunk_index")]
        indexes = [
            models.Index(
                fields=["collection_id"],
                name="knowledge_chunk_collection_idx",
            ),
        ]
        db_table_comment = (
            "Chunks indexados de documentos institucionais com embeddings pgvector. "
            "Consultado pelo RAGService via l2_distance para busca semântica."
        )

    def __str__(self) -> str:
        return f"Chunk {self.chunk_index} — {self.document.title}"
