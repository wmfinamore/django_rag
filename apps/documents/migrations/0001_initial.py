"""
Migration inicial da app documents.

Cria:
    - documents_userdocument  (documentos pessoais dos usuarios)
    - documents_userchunk     (chunks com embedding pgvector)

Requer:
    - pgvector instalado no PostgreSQL (extensao criada pela migration knowledge.0001)
"""

import uuid

import django.db.models.deletion
import pgvector.django
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        # Garante que a extensao vector ja foi criada pelo knowledge
        ("knowledge", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserDocument",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="criado em")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="atualizado em")),
                (
                    "id",
                    models.UUIDField(
                        db_comment="UUID do documento pessoal.",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        db_comment="Titulo descritivo do documento pessoal.",
                        help_text="Titulo descritivo do documento exibido nas respostas do RAG como fonte.",
                        max_length=300,
                        verbose_name="titulo",
                    ),
                ),
                (
                    "file",
                    models.FileField(
                        db_comment="Caminho relativo do arquivo dentro do MEDIA_ROOT.",
                        help_text="Arquivo do documento (pdf, docx, txt, md). Armazenado em MEDIA_ROOT.",
                        upload_to="documents/%Y/%m/",
                        verbose_name="arquivo",
                    ),
                ),
                (
                    "file_type",
                    models.CharField(
                        choices=[("pdf", "PDF"), ("docx", "DOCX"), ("txt", "TXT"), ("md", "Markdown")],
                        db_comment="Formato do arquivo (pdf, docx, txt, md).",
                        help_text="Formato do arquivo para extracao de texto.",
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
                        db_comment="Estado de processamento: pending -> indexing -> ready/error.",
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
                        db_comment="Total de chunks indexados no pgvector.",
                        default=0,
                        help_text="Quantidade de chunks gerados apos indexacao bem-sucedida.",
                        verbose_name="numero de chunks",
                    ),
                ),
                (
                    "error_message",
                    models.TextField(
                        blank=True,
                        db_comment="Mensagem de erro da ultima tentativa falha.",
                        default="",
                        help_text="Detalhes do erro ocorrido durante a indexacao.",
                        verbose_name="mensagem de erro",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        db_comment="Usuario dono do documento.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="documents",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="proprietario",
                    ),
                ),
            ],
            options={
                "verbose_name": "documento pessoal",
                "verbose_name_plural": "documentos pessoais",
                "db_table_comment": (
                    "Documentos pessoais dos usuarios indexados no pipeline RAG. "
                    "Cada documento origina N UserChunk com embedding pgvector. "
                    "Visivel apenas ao proprietario."
                ),
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["owner", "status"], name="user_doc_owner_status_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="UserChunk",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_comment="UUID do chunk pessoal.",
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "user_id",
                    models.UUIDField(
                        db_comment=(
                            "UUID desnormalizado do CustomUser para filtrar chunks "
                            "por usuario sem JOIN no momento da busca vetorial."
                        ),
                        db_index=True,
                        help_text="UUID do proprietario (desnormalizado para buscas eficientes no pgvector).",
                        verbose_name="ID do usuario",
                    ),
                ),
                (
                    "chunk_index",
                    models.IntegerField(
                        db_comment="Ordem do chunk dentro do documento (0-based).",
                        help_text="Posicao do chunk no documento original (0-based).",
                        verbose_name="indice do chunk",
                    ),
                ),
                (
                    "content",
                    models.TextField(
                        db_comment="Texto do chunk pos-mascaramento Presidio.",
                        help_text="Texto do chunk apos extracao e mascaramento PII/LGPD.",
                        verbose_name="conteudo",
                    ),
                ),
                (
                    "embedding",
                    pgvector.django.VectorField(
                        db_comment="Embedding pgvector (384d, all-MiniLM-L6-v2) para busca L2.",
                        dimensions=384,
                        help_text="Vetor de 384 dimensoes gerado pelo modelo all-MiniLM-L6-v2.",
                        verbose_name="embedding",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        db_comment="Documento do qual este chunk foi extraido.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chunks",
                        to="documents.userdocument",
                        verbose_name="documento",
                    ),
                ),
            ],
            options={
                "verbose_name": "chunk pessoal",
                "verbose_name_plural": "chunks pessoais",
                "db_table_comment": (
                    "Chunks indexados de documentos pessoais com embeddings pgvector. "
                    "Consultado pelo RAGService via l2_distance para busca semantica "
                    "quando use_personal_docs=True na conversa."
                ),
                "ordering": ["document", "chunk_index"],
                "indexes": [
                    models.Index(fields=["user_id"], name="user_chunk_user_idx"),
                ],
                "unique_together": {("document", "chunk_index")},
            },
        ),
    ]
