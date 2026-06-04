"""
Modelos da app knowledge.

Hierarquia:
    KnowledgeCollection  ->  KnowledgeDocument  ->  KnowledgeChunk
    KnowledgeCollection  ->  BulkImportJob

Integracao:
    - apps.core.tasks.index_document       -- preenche KnowledgeChunk
    - apps.knowledge.tasks.run_bulk_import -- carga em lote de diretorio
    - apps.core.rag_service.RAGService     -- consulta KnowledgeChunk via l2_distance
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import Group
from django.db import models
from pgvector.django import VectorField

from apps.core.models import TimeStampedModel


class KnowledgeCollection(TimeStampedModel):
    """Colecao de documentos institucionais com controle de acesso por grupos Django."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False,
                          db_comment="UUID da colecao.")
    name = models.CharField("nome", max_length=200, unique=True,
                             help_text="Nome unico da colecao (ex: 'RH - Politicas Internas').",
                             db_comment="Nome unico da colecao institucional.")
    description = models.TextField("descricao", blank=True, default="",
                                   help_text="Descricao do conteudo e finalidade da colecao.",
                                   db_comment="Texto livre descrevendo o conteudo da colecao.")
    allowed_groups = models.ManyToManyField(
        Group, verbose_name="grupos com acesso", blank=True,
        related_name="knowledge_collections",
        help_text=(
            "Grupos Django que podem acessar esta colecao. "
            "Deixe em branco para acesso publico a qualquer usuario autenticado."
        ),
    )
    is_active = models.BooleanField("ativa", default=True,
                                    help_text="Colecoes inativas sao ignoradas pelo pipeline RAG.",
                                    db_comment="Flag de ativacao; FALSE exclui das buscas RAG.")

    class Meta:
        verbose_name = "colecao de conhecimento"
        verbose_name_plural = "colecoes de conhecimento"
        ordering = ["name"]
        db_table_comment = (
            "Colecoes de documentos institucionais. Controla acesso via "
            "grupos Django e agrupa KnowledgeDocument para fins de RAG."
        )

    def __str__(self) -> str:
        return self.name

    def is_accessible_by(self, user) -> bool:
        """Verifica se user tem acesso a colecao (superuser, publico ou grupo)."""
        if not self.is_active:
            return False
        if user.is_superuser:
            return True
        allowed = self.allowed_groups.all()
        if not allowed.exists():
            return True
        return user.groups.filter(pk__in=allowed).exists()


class KnowledgeDocument(TimeStampedModel):
    """
    Documento institucional associado a uma KnowledgeCollection.

    Ciclo de vida: pending -> indexing -> ready / error

    Origem:
        - Upload manual via API: bulk_import_job=None.
        - Carga em lote via BulkImportJob: bulk_import_job preenchido.
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
                          db_comment="UUID do documento.")
    collection = models.ForeignKey(
        KnowledgeCollection, on_delete=models.CASCADE, related_name="documents",
        verbose_name="colecao", db_comment="Colecao a qual este documento pertence.",
    )
    title = models.CharField("titulo", max_length=300,
                              help_text="Titulo descritivo exibido nas respostas do RAG como fonte.",
                              db_comment="Titulo descritivo do documento usado como fonte no RAG.")
    file_path = models.CharField(
        "caminho do arquivo", max_length=1000,
        help_text="Caminho absoluto do arquivo no filesystem (acessivel pelo worker Celery).",
        db_comment="Caminho absoluto do arquivo no servidor.",
    )
    file_type = models.CharField("tipo de arquivo", max_length=10, choices=FileType.choices,
                                  help_text="Formato do arquivo para extracao de texto.",
                                  db_comment="Formato do arquivo (pdf, docx, txt, md).")
    status = models.CharField(
        "status", max_length=20, choices=Status.choices, default=Status.PENDING,
        db_index=True, help_text="Estado atual do processamento do documento.",
        db_comment="Estado de processamento: pending -> indexing -> ready/error.",
    )
    chunks_count = models.IntegerField("numero de chunks", default=0,
                                       help_text="Quantidade de chunks gerados apos indexacao bem-sucedida.",
                                       db_comment="Total de chunks indexados no pgvector.")
    error_message = models.TextField("mensagem de erro", blank=True, default="",
                                     help_text="Detalhes do erro ocorrido durante a indexacao.",
                                     db_comment="Mensagem de erro da ultima tentativa falha.")
    ingested_by = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="ingested_documents", verbose_name="ingerido por",
        db_comment="Usuario que realizou o upload/ingestion do documento.",
    )
    bulk_import_job = models.ForeignKey(
        "knowledge.BulkImportJob", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="documents", verbose_name="job de importacao em lote",
        db_comment="Job de carga em lote que originou este documento (NULL para uploads manuais).",
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
            "Cada documento origina N KnowledgeChunk com embedding pgvector. "
            "Pode ser originado por upload HTTP ou por carga em lote via BulkImportJob."
        )

    def __str__(self) -> str:
        return f"{self.title} [{self.get_status_display()}]"

    @property
    def is_ready(self) -> bool:
        return self.status == self.Status.READY

    def trigger_indexing(self) -> str:
        from apps.core.tasks import index_document
        result = index_document.delay(str(self.id), "knowledge")
        return result.id

    def trigger_reindex(self) -> str:
        from apps.core.tasks import reindex_document
        result = reindex_document.delay(str(self.id), "knowledge")
        return result.id


class BulkImportJob(TimeStampedModel):
    """
    Job de carga em lote de documentos a partir de um diretorio no disco.

    Fluxo de status: pending -> running -> completed / completed_with_errors / failed

    Criado via:
        - API: POST /api/knowledge/collections/<id>/bulk-import/
        - CLI: python manage.py bulk_ingest_knowledge <collection_id> <directory>

    A task Celery apps.knowledge.tasks.run_bulk_import executa o job:
    percorre source_directory, cria KnowledgeDocuments e enfileira indexacao.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        RUNNING = "running", "Executando"
        COMPLETED = "completed", "Concluido"
        COMPLETED_WITH_ERRORS = "completed_with_errors", "Concluido com erros"
        FAILED = "failed", "Falha"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False,
                          db_comment="UUID do job de importacao em lote.")
    collection = models.ForeignKey(
        KnowledgeCollection, on_delete=models.CASCADE, related_name="bulk_import_jobs",
        verbose_name="colecao", db_comment="Colecao de destino dos documentos importados.",
    )
    source_directory = models.CharField(
        "diretorio de origem", max_length=1000,
        help_text=(
            "Caminho absoluto do diretorio no servidor onde estao os documentos. "
            "Deve ser acessivel pelo worker Celery."
        ),
        db_comment="Caminho absoluto do diretorio de origem dos documentos.",
    )
    recursive = models.BooleanField(
        "recursivo", default=True,
        help_text="Se marcado, percorre subdiretorios recursivamente.",
        db_comment="Indica se a varredura deve ser recursiva.",
    )
    file_extensions = models.JSONField(
        "extensoes aceitas", default=list, blank=True,
        help_text=(
            'Lista de extensoes sem ponto (ex: ["pdf", "docx"]). '
            "Vazio = aceita todas: pdf, docx, txt, md."
        ),
        db_comment="Filtro de extensoes; lista vazia = todas as extensoes suportadas.",
    )
    status = models.CharField(
        "status", max_length=30, choices=Status.choices, default=Status.PENDING,
        db_index=True, help_text="Estado atual do job de importacao.",
        db_comment="Estado do job: pending -> running -> completed/completed_with_errors/failed.",
    )
    total_files = models.IntegerField("total de arquivos", default=0,
                                      help_text="Total de arquivos encontrados no diretorio.",
                                      db_comment="Total de arquivos elegiveis para importacao.")
    indexed_files = models.IntegerField(
        "arquivos enfileirados", default=0,
        help_text="Arquivos cujos documentos foram criados e enfileirados para indexacao.",
        db_comment="Documentos criados com sucesso e indexacao enfileirada.",
    )
    failed_files = models.IntegerField(
        "arquivos com falha", default=0,
        help_text="Arquivos que falharam durante a criacao do documento.",
        db_comment="Arquivos que geraram erro ao tentar criar o KnowledgeDocument.",
    )
    error_message = models.TextField(
        "mensagem de erro", blank=True, default="",
        help_text="Detalhes do erro fatal (preenchido quando status=failed).",
        db_comment="Mensagem de erro quando o job falha antes de completar a varredura.",
    )
    celery_task_id = models.CharField(
        "ID da task Celery", max_length=255, blank=True, default="",
        help_text="ID da task Celery que executa este job.",
        db_comment="Task ID retornado pelo Celery ao disparar run_bulk_import.",
    )
    triggered_by = models.ForeignKey(
        "accounts.CustomUser", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="bulk_import_jobs", verbose_name="disparado por",
        db_comment="Usuario que iniciou o job de importacao em lote.",
    )

    class Meta:
        verbose_name = "job de importacao em lote"
        verbose_name_plural = "jobs de importacao em lote"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["collection", "status"], name="knowledge_bulkjob_coll_status_idx"),
        ]
        db_table_comment = (
            "Jobs de carga em lote de documentos a partir de um diretorio no disco. "
            "Cada job cria N KnowledgeDocuments na colecao de destino."
        )

    def __str__(self) -> str:
        return (
            f"BulkImport [{self.get_status_display()}] "
            f"-> {self.collection.name} ({self.source_directory})"
        )

    @property
    def is_done(self) -> bool:
        return self.status in (
            self.Status.COMPLETED,
            self.Status.COMPLETED_WITH_ERRORS,
            self.Status.FAILED,
        )

    @property
    def progress_pct(self) -> int:
        if not self.total_files:
            return 0
        done = self.indexed_files + self.failed_files
        return min(100, int(done / self.total_files * 100))


class KnowledgeChunk(models.Model):
    """
    Trecho (chunk) de um KnowledgeDocument com embedding pgvector.

    Gerado automaticamente por apps.core.tasks.index_document.
    Nao deve ser criado/editado manualmente.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False,
                          db_comment="UUID do chunk.")
    document = models.ForeignKey(
        KnowledgeDocument, on_delete=models.CASCADE, related_name="chunks",
        verbose_name="documento", db_comment="Documento do qual este chunk foi extraido.",
    )
    collection_id = models.UUIDField(
        "ID da colecao", db_index=True,
        help_text="UUID da colecao (desnormalizado para buscas eficientes no pgvector).",
        db_comment=(
            "UUID desnormalizado da KnowledgeCollection para filtrar chunks "
            "por colecao sem JOIN no momento da busca vetorial."
        ),
    )
    chunk_index = models.IntegerField("indice do chunk",
                                      help_text="Posicao do chunk no documento original (0-based).",
                                      db_comment="Ordem do chunk dentro do documento (0-based).")
    content = models.TextField("conteudo",
                                help_text="Texto do chunk apos extracao e mascaramento PII/LGPD.",
                                db_comment="Texto do chunk pos-mascaramento Presidio.")
    embedding = VectorField("embedding", dimensions=384,
                             help_text="Vetor de 384 dimensoes gerado pelo modelo all-MiniLM-L6-v2.",
                             db_comment="Embedding pgvector (384d, all-MiniLM-L6-v2) para busca L2.")

    class Meta:
        verbose_name = "chunk de conhecimento"
        verbose_name_plural = "chunks de conhecimento"
        ordering = ["document", "chunk_index"]
        unique_together = [("document", "chunk_index")]
        indexes = [
            models.Index(fields=["collection_id"], name="knowledge_chunk_collection_idx"),
        ]
        db_table_comment = (
            "Chunks indexados de documentos institucionais com embeddings pgvector. "
            "Consultado pelo RAGService via l2_distance para busca semantica."
        )

    def __str__(self) -> str:
        return f"Chunk {self.chunk_index} -- {self.document.title}"
