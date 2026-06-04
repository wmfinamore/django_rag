"""
Migration 0002: adiciona BulkImportJob e vincula KnowledgeDocument ao job.

Alterações:
    - Cria a tabela knowledge_bulkimportjob com todos os campos de rastreamento
      de importação em lote (source_directory, recursive, file_extensions,
      status, contadores, celery_task_id, triggered_by).
    - Adiciona FK nullable bulk_import_job em KnowledgeDocument (SET_NULL).
    - Adiciona índice (collection, status) em BulkImportJob.

Esta migration é compatível com bancos existentes: o campo bulk_import_job
em KnowledgeDocument é nullable, portanto os documentos já existentes não
são afetados.
"""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ------------------------------------------------------------------ #
        # BulkImportJob                                                        #
        # ------------------------------------------------------------------ #
        migrations.CreateModel(
            name="BulkImportJob",
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
                        primary_key=True,
                        default=uuid.uuid4,
                        editable=False,
                        serialize=False,
                        db_comment="UUID do job de importação em lote.",
                    ),
                ),
                (
                    "source_directory",
                    models.CharField(
                        max_length=1000,
                        verbose_name="diretório de origem",
                        help_text=(
                            "Caminho absoluto do diretório no servidor onde estão os documentos. "
                            "Deve ser acessível pelo worker Celery."
                        ),
                        db_comment="Caminho absoluto do diretório de origem dos documentos.",
                    ),
                ),
                (
                    "recursive",
                    models.BooleanField(
                        default=True,
                        verbose_name="recursivo",
                        help_text="Se marcado, percorre subdiretórios recursivamente.",
                        db_comment="Indica se a varredura deve ser recursiva (incluir subdiretórios).",
                    ),
                ),
                (
                    "file_extensions",
                    models.JSONField(
                        default=list,
                        blank=True,
                        verbose_name="extensões aceitas",
                        help_text=(
                            'Lista de extensões de arquivo aceitas, sem ponto (ex: ["pdf", "docx"]). '
                            "Vazio = aceita todas as extensões suportadas (pdf, docx, txt, md)."
                        ),
                        db_comment="Filtro de extensões; lista vazia = todas as extensões suportadas.",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        max_length=30,
                        choices=[
                            ("pending", "Pendente"),
                            ("running", "Executando"),
                            ("completed", "Concluído"),
                            ("completed_with_errors", "Concluído com erros"),
                            ("failed", "Falha"),
                        ],
                        default="pending",
                        db_index=True,
                        verbose_name="status",
                        help_text="Estado atual do job de importação.",
                        db_comment="Estado do job: pending → running → completed/completed_with_errors/failed.",
                    ),
                ),
                (
                    "total_files",
                    models.IntegerField(
                        default=0,
                        verbose_name="total de arquivos",
                        help_text="Total de arquivos encontrados no diretório.",
                        db_comment="Total de arquivos encontrados e elegíveis para importação.",
                    ),
                ),
                (
                    "indexed_files",
                    models.IntegerField(
                        default=0,
                        verbose_name="arquivos enfileirados",
                        help_text="Quantidade de arquivos cujos documentos foram criados e enfileirados para indexação.",
                        db_comment="Documentos criados com sucesso e indexação enfileirada.",
                    ),
                ),
                (
                    "failed_files",
                    models.IntegerField(
                        default=0,
                        verbose_name="arquivos com falha",
                        help_text="Quantidade de arquivos que falharam durante a criação do documento.",
                        db_comment="Arquivos que geraram erro ao tentar criar o KnowledgeDocument.",
                    ),
                ),
                (
                    "error_message",
                    models.TextField(
                        blank=True,
                        default="",
                        verbose_name="mensagem de erro",
                        help_text="Detalhes do erro fatal (preenchido quando status=failed).",
                        db_comment="Mensagem de erro quando o job falha antes de completar a varredura.",
                    ),
                ),
                (
                    "celery_task_id",
                    models.CharField(
                        max_length=255,
                        blank=True,
                        default="",
                        verbose_name="ID da task Celery",
                        help_text="ID da task Celery que executa este job (útil para monitoramento).",
                        db_comment="Task ID retornado pelo Celery ao disparar run_bulk_import.",
                    ),
                ),
                (
                    "collection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bulk_import_jobs",
                        to="knowledge.knowledgecollection",
                        verbose_name="coleção",
                        db_comment="Coleção de destino dos documentos importados.",
                    ),
                ),
                (
                    "triggered_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.SET_NULL,
                        null=True,
                        blank=True,
                        related_name="bulk_import_jobs",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="disparado por",
                        db_comment="Usuário que iniciou o job de importação em lote.",
                    ),
                ),
            ],
            options={
                "verbose_name": "job de importação em lote",
                "verbose_name_plural": "jobs de importação em lote",
                "ordering": ["-created_at"],
                "db_table_comment": (
                    "Jobs de carga em lote de documentos a partir de um diretório no disco. "
                    "Cada job cria N KnowledgeDocuments na coleção de destino."
                ),
            },
        ),
        migrations.AddIndex(
            model_name="bulkimportjob",
            index=models.Index(
                fields=["collection", "status"],
                name="knowledge_bulkjob_coll_status_idx",
            ),
        ),

        # ------------------------------------------------------------------ #
        # KnowledgeDocument — FK para BulkImportJob (nullable)                #
        # ------------------------------------------------------------------ #
        migrations.AddField(
            model_name="knowledgedocument",
            name="bulk_import_job",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="documents",
                to="knowledge.bulkimportjob",
                verbose_name="job de importação em lote",
                db_comment="Job de carga em lote que originou este documento (NULL para uploads manuais).",
            ),
        ),
    ]
