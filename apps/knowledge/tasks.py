"""
Tasks Celery da app knowledge.

run_bulk_import — executa um BulkImportJob: percorre o diretório de origem,
                  cria KnowledgeDocuments e enfileira apps.core.tasks.index_document
                  para cada arquivo encontrado.

Esta task é disparada:
    1. Via API: POST /api/knowledge/collections/<id>/bulk-import/
    2. Via management command: python manage.py bulk_ingest_knowledge
       (o command cria o job e usa sync=True ou enfileira diretamente)
"""

from __future__ import annotations

import logging
import pathlib

from celery import shared_task
from django.db import transaction

from apps.core.tasks import index_document
from apps.knowledge.models import BulkImportJob, KnowledgeDocument

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {"pdf", "docx", "txt", "md"}


@shared_task(bind=True, max_retries=0, name="knowledge.run_bulk_import")
def run_bulk_import(self, job_id: str) -> dict:
    """
    Executa um BulkImportJob identificado por ``job_id``.

    1. Carrega o BulkImportJob e marca como RUNNING.
    2. Percorre ``source_directory`` (recursivo se job.recursive=True).
    3. Para cada arquivo com extensão aceita:
       - Cria um KnowledgeDocument vinculado ao job.
       - Enfileira apps.core.tasks.index_document.
    4. Atualiza contadores e muda status para COMPLETED ou COMPLETED_WITH_ERRORS.
    5. Em caso de erro fatal: marca status=FAILED com mensagem de erro.

    Retorna um dicionário de sumário com os contadores finais.
    """
    # ------------------------------------------------------------------
    # Carrega o job
    # ------------------------------------------------------------------
    try:
        job = BulkImportJob.objects.select_related("collection", "triggered_by").get(pk=job_id)
    except BulkImportJob.DoesNotExist:
        logger.error("BulkImportJob não encontrado: %s", job_id)
        return {"error": f"Job não encontrado: {job_id}"}

    # Armazena o task_id do Celery no job para rastreamento
    BulkImportJob.objects.filter(pk=job.pk).update(
        status=BulkImportJob.Status.RUNNING,
        celery_task_id=self.request.id or "",
    )
    job.refresh_from_db()

    collection = job.collection
    source_dir = pathlib.Path(job.source_directory)

    # ------------------------------------------------------------------
    # Valida diretório
    # ------------------------------------------------------------------
    if not source_dir.exists() or not source_dir.is_dir():
        error_msg = f"Diretório inválido ou não acessível: {source_dir}"
        logger.error("BulkImportJob %s falhou: %s", job_id, error_msg)
        BulkImportJob.objects.filter(pk=job.pk).update(
            status=BulkImportJob.Status.FAILED,
            error_message=error_msg,
        )
        return {"job_id": job_id, "status": "failed", "error": error_msg}

    # ------------------------------------------------------------------
    # Define extensões aceitas
    # ------------------------------------------------------------------
    accepted_exts: set[str] = (
        set(job.file_extensions) & SUPPORTED_EXTENSIONS
        if job.file_extensions
        else SUPPORTED_EXTENSIONS
    )

    # ------------------------------------------------------------------
    # Varredura do diretório
    # ------------------------------------------------------------------
    pattern = "**/*" if job.recursive else "*"
    files = sorted(
        p for p in source_dir.glob(pattern)
        if p.is_file() and p.suffix.lstrip(".").lower() in accepted_exts
    )

    total = len(files)
    BulkImportJob.objects.filter(pk=job.pk).update(total_files=total)

    if total == 0:
        logger.info("BulkImportJob %s: nenhum arquivo encontrado em %s", job_id, source_dir)
        BulkImportJob.objects.filter(pk=job.pk).update(
            status=BulkImportJob.Status.COMPLETED,
        )
        return {"job_id": job_id, "status": "completed", "total": 0, "indexed": 0, "failed": 0}

    logger.info("BulkImportJob %s: %d arquivos encontrados em %s", job_id, total, source_dir)

    # ------------------------------------------------------------------
    # Processa cada arquivo
    # ------------------------------------------------------------------
    indexed = 0
    failed = 0

    for file_path in files:
        ext = file_path.suffix.lstrip(".").lower()
        title = file_path.stem.replace("_", " ").replace("-", " ")

        try:
            with transaction.atomic():
                doc = KnowledgeDocument.objects.create(
                    collection=collection,
                    title=title,
                    file_path=str(file_path),
                    file_type=ext,
                    status=KnowledgeDocument.Status.PENDING,
                    ingested_by=job.triggered_by,
                    bulk_import_job=job,
                )

            index_document.delay(str(doc.id), "knowledge")
            indexed += 1
            logger.debug("Enfileirado: %s → doc=%s", file_path.name, doc.id)

        except Exception as exc:
            failed += 1
            logger.error("Falha ao processar %s: %s", file_path, exc)

        # Atualiza contadores a cada arquivo para progresso em tempo real
        if (indexed + failed) % 10 == 0 or (indexed + failed) == total:
            BulkImportJob.objects.filter(pk=job.pk).update(
                indexed_files=indexed,
                failed_files=failed,
            )

    # ------------------------------------------------------------------
    # Status final
    # ------------------------------------------------------------------
    final_status = (
        BulkImportJob.Status.COMPLETED
        if failed == 0
        else BulkImportJob.Status.COMPLETED_WITH_ERRORS
    )

    BulkImportJob.objects.filter(pk=job.pk).update(
        status=final_status,
        indexed_files=indexed,
        failed_files=failed,
    )

    logger.info(
        "BulkImportJob %s finalizado: status=%s indexed=%d failed=%d",
        job_id, final_status, indexed, failed,
    )

    return {
        "job_id": job_id,
        "collection": str(collection.id),
        "status": final_status,
        "total": total,
        "indexed": indexed,
        "failed": failed,
    }
