"""
Migration inicial da app knowledge.

Cria:
    - knowledge_knowledgecollection         (coleções institucionais)
    - knowledge_knowledgecollection_allowed_groups (M2M: coleção ↔ grupo)
    - knowledge_knowledgedocument           (documentos por coleção)
    - knowledge_knowledgechunk              (chunks com embedding pgvector)

Requer:
    - pgvector instalado no PostgreSQL (CREATE EXTENSION IF NOT EXISTS vector)
    - Extensão habilitada via migration de dependência (accounts.0001_initial ou similar)
"""

import uuid

import django.db.models.deletion
import pgvector.django
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ------------------------------------------------------------------ #
        # Habilita a extensão pgvector no PostgreSQL                         #
        # ------------------------------------------------------------------ #
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS vector;",
            reverse_sql="DROP EXTENSION IF EXISTS vector;",
        ),

        # ------------------------------------------------------------------ #
        # KnowledgeCollection                                                 #
        # ------------------------------------------------------------------ #
        migrations.CreateModel(
            name="KnowledgeCollection",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_comment="Timestamp de criação do registro (UTC, preenchido automaticamente).",
                        verbose_name="criado em",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        db_comment="Timestamp da última atualização do registro (UTC, atualizado automaticamente).",
                        verbose_name="atualizado em",
                    ),
                ),
                (
                    "id",
                    models.UUIDField(
                        db_comment="UUID da coleção gerado automaticamente.",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        db_comment="Nome único da coleção institucional.",
                        help_text="Nome único da coleção (ex: 'RH – Políticas Internas').",
                        max_length=200,
                        unique=True,
                        verbose_name="nome",
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        db_comment="Texto livre descrevendo o conteúdo da coleção.",
                        default="",
                        help_text="Descrição do conteúdo e finalidade da coleção.",
                        verbose_name="descrição",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        db_comment="Flag de ativação; FALSE exclui a coleção das buscas RAG.",
                        default=True,
                        help_text="Coleções inativas são ignoradas pelo pipeline RAG.",
                        verbose_name="ativa",
                    ),
                ),
                (
                    "allowed_groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text=(
                            "Grupos Django que podem acessar esta coleção. "
                            "Deixe em branco para acesso público a qualquer usuário autenticado."
                        ),
                        related_name="knowledge_collections",
                        to="auth.group",
                        verbose_name="grupos com acesso",
                    ),
                ),
            ],
            options={
                "verbose_name": "coleção de conhecimento",
                "verbose_name_plural": "coleções de conhecimento",
                "ordering": ["name"],
                "db_table_comment": (
                    "Coleções de documentos institucionais. Controla acesso via "
                    "grupos Django e agrupa KnowledgeDocument para fins de RAG."
                ),
            },
        ),

        # ------------------------------------------------------------------ #
        # KnowledgeDocument                                                   #
        # ------------------------------------------------------------------ #
        migrations.CreateModel(
            name="KnowledgeDocument",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_comment="Timestamp de criação do registro (UTC, preenchido automaticamente).",
                        verbose_name="criado em",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        db_comment="Timestamp da última atualização do registro (UTC, atualizado automaticamente).",
                        verbose_name="atualizado em",
                    ),
                ),
                (
                    "id",
                    models.UUIDField(
                        db_comment="UUID do documento gerado automaticamente.",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        db_comment="Título descritivo do documento usado como fonte no RAG.",
                        help_text="Título descritivo exibido nas respostas do RAG como fonte.",
                        max_length=300,
                        verbose_name="título",
                    ),
                ),
                (
                    "file_path",
                    models.CharField(
                        db_comment="Caminho absoluto do arquivo no servidor.",
                        help_text="Caminho absoluto do arquivo no filesystem (acessível pelo worker Celery).",
                        max_length=1000,
                        verbose_name="caminho do arquivo",
                    ),
                ),
                (
                    "file_type",
                    models.CharField(
                        choices=[
                            ("pdf", "PDF"),
                            ("docx", "DOCX"),
                            ("txt", "TXT"),
                            ("md", "Markdown"),
                        ],
                        db_comment="Formato do arquivo (pdf, docx, txt, md).",
                        help_text="Formato do arquivo para extração de texto.",
                        max_length=10,
                        verbose_name="tipo de arquivo",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pendente"),
                            ("indexing", "Indexando"),
                            ("ready", "Pronto"),
                            ("error", "Erro"),
                        ],
                        db_comment="Estado de processamento: pending → indexing → ready/error.",
                        db_index=True,
                        default="pending",
                        help_text="Estado atual do processamento do documento.",
                        max_length=20,
                        verbose_name="status",
                    ),
                ),
                (
                    "chunks_count",
                    models.IntegerField(
                        db_comment="Total de chunks indexados no pgvector para este documento.",
                        default=0,
                        help_text="Quantidade de chunks gerados após indexação bem-sucedida.",
                        verbose_name="número de chunks",
                    ),
                ),
                (
                    "error_message",
                    models.TextField(
                        blank=True,
                        db_comment="Mensagem de erro da última tentativa de indexação falha.",
                        default="",
                        help_text="Detalhes do erro ocorrido durante a indexação (quando status=error).",
                        verbose_name="mensagem de erro",
                    ),
                ),
                (
                    "collection",
                    models.ForeignKey(
                        db_comment="Coleção à qual este documento pertence.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="documents",
                        to="knowledge.knowledgecollection",
                        verbose_name="coleção",
                    ),
                ),
                (
                    "ingested_by",
                    models.ForeignKey(
                        blank=True,
                        db_comment="Usuário que realizou o upload/ingestão do documento.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ingested_documents",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="ingerido por",
                    ),
                ),
            ],
            options={
                "verbose_name": "documento de conhecimento",
                "verbose_name_plural": "documentos de conhecimento",
                "ordering": ["-created_at"],
                "db_table_comment": (
                    "Documentos institucionais indexados no pipeline RAG. "
                    "Cada documento origina N KnowledgeChunk com embedding pgvector."
                ),
            },
        ),
        migrations.AddIndex(
            model_name="knowledgedocument",
            index=models.Index(
                fields=["collection", "status"],
                name="knowledge_doc_coll_status_idx",
            ),
        ),

        # ------------------------------------------------------------------ #
        # KnowledgeChunk                                                      #
        # ------------------------------------------------------------------ #
        migrations.CreateModel(
            name="KnowledgeChunk",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_comment="UUID do chunk gerado automaticamente.",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "collection_id",
                    models.UUIDField(
                        db_comment=(
                            "UUID desnormalizado da KnowledgeCollection para filtrar chunks "
                            "por coleção sem JOIN no momento da busca vetorial."
                        ),
                        db_index=True,
                        help_text="UUID da coleção (desnormalizado para buscas eficientes no pgvector).",
                        verbose_name="ID da coleção",
                    ),
                ),
                (
                    "chunk_index",
                    models.IntegerField(
                        db_comment="Ordem do chunk dentro do documento (0-based).",
                        help_text="Posição do chunk no documento original (0-based).",
                        verbose_name="índice do chunk",
                    ),
                ),
                (
                    "content",
                    models.TextField(
                        db_comment="Texto do chunk pós-mascaramento Presidio.",
                        help_text="Texto do chunk após extração e mascaramento PII/LGPD.",
                        verbose_name="conteúdo",
                    ),
                ),
                (
                    "embedding",
                    pgvector.django.VectorField(
                        db_comment="Embedding pgvector (384d, all-MiniLM-L6-v2) para busca semântica L2.",
                        dimensions=384,
                        help_text="Vetor de 384 dimensões gerado pelo modelo all-MiniLM-L6-v2.",
                        verbose_name="embedding",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        db_comment="Documento do qual este chunk foi extraído.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="knowledge.knowledgedocument",
                        verbose_name="documento",
                    ),
                ),
            ],
            options={
                "verbose_name": "chunk de conhecimento",
                "verbose_name_plural": "chunks de conhecimento",
                "ordering": ["document", "chunk_index"],
                "db_table_comment": (
                    "Chunks indexados de documentos institucionais com embeddings pgvector. "
                    "Consultado pelo RAGService via l2_distance para busca semântica."
                ),
            },
        ),
        migrations.AddConstraint(
            model_name="knowledgechunk",
            constraint=models.UniqueConstraint(
                fields=["document", "chunk_index"],
                name="knowledge_knowledgechunk_document_chunk_index_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgechunk",
            index=models.Index(
                fields=["collection_id"],
                name="knowledge_chunk_collection_idx",
            ),
        ),
    ]
