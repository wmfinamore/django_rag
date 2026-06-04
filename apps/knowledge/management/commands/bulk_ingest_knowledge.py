"""
Management command: bulk_ingest_knowledge
=========================================

Carrega documentos de um diretório do disco para uma KnowledgeCollection,
criando um BulkImportJob que rastreia o progresso e disparando a indexação
Celery para cada arquivo encontrado.

Uso::

    # Importa todos os arquivos suportados do diretório para a coleção <uuid>
    python manage.py bulk_ingest_knowledge <collection_id> /caminho/do/diretorio

    # Importa apenas PDFs e DOCX, sem recursividade
    python manage.py bulk_ingest_knowledge <collection_id> /caminho --extensions pdf docx --no-recursive

    # Importa de forma síncrona (sem Celery — útil para debug/testes)
    python manage.py bulk_ingest_knowledge <collection_id> /caminho --sync

    # Ignora arquivos já presentes na coleção (por file_path)
    python manage.py bulk_ingest_knowledge <collection_id> /caminho --skip-existing

    # Seca — mostra o que seria importado sem criar nada
    python manage.py bulk_ingest_knowledge <collection_id> /caminho --dry-run

Opções:
    --extensions    Extensões aceitas sem ponto (ex: pdf docx). Padrão: todas suportadas.
    --no-recursive  Não percorre subdiretórios.
    --skip-existing Ignora arquivos cujo file_path já existe na coleção.
    --dry-run       Apenas lista os arquivos que seriam importados.
    --sync          Executa a indexação de forma síncrona (sem Celery).
    --batch-size    Quantidade de documentos a criar por transação (padrão: 100).
    --user          Username do usuário a registrar como triggered_by (padrão: None).
"""

from __future__ import annotations

import logging
import pathlib
from typing import Iterator

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {"pdf", "docx", "txt", "md"}


class Command(BaseCommand):
    help = (
        "Importa documentos de um diretório para uma KnowledgeCollection, "
        "criando um BulkImportJob e enfileirando a indexação Celery."
    )

    # ------------------------------------------------------------------
    # Definição dos argumentos
    # ------------------------------------------------------------------

    def add_arguments(self, parser):
        parser.add_argument(
            "collection_id",
            type=str,
            help="UUID da KnowledgeCollection de destino.",
        )
        parser.add_argument(
            "directory",
            type=str,
            help="Caminho absoluto do diretório contendo os documentos.",
        )
        parser.add_argument(
            "--extensions",
            nargs="+",
            metavar="EXT",
            default=[],
            help=(
                "Extensões de arquivo aceitas (sem ponto). "
                f"Padrão: todas suportadas ({', '.join(sorted(SUPPORTED_EXTENSIONS))})."
            ),
        )
        parser.add_argument(
            "--no-recursive",
            dest="recursive",
            action="store_false",
            default=True,
            help="Não percorre subdiretórios.",
        )
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            default=False,
            help="Ignora arquivos cujo caminho absoluto já existe na coleção.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Lista os arquivos que seriam importados sem criar nenhum registro.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            default=False,
            help="Executa a indexação de forma síncrona (sem Celery). Útil para debug.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=100,
            metavar="N",
            help="Quantidade de documentos a criar por transação de banco (padrão: 100).",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            metavar="USERNAME",
            help="Username do usuário a registrar como triggered_by no BulkImportJob.",
        )

    # ------------------------------------------------------------------
    # Ponto de entrada
    # ------------------------------------------------------------------

    def handle(self, *args, **options):
        from apps.knowledge.models import BulkImportJob, KnowledgeCollection, KnowledgeDocument

        collection_id: str = options["collection_id"]
        directory: str = options["directory"]
        extensions: list[str] = [e.lower().lstrip(".") for e in options["extensions"]]
        recursive: bool = options["recursive"]
        skip_existing: bool = options["skip_existing"]
        dry_run: bool = options["dry_run"]
        sync: bool = options["sync"]
        batch_size: int = options["batch_size"]
        username: str | None = options["user"]

        # --- Valida coleção ---
        try:
            collection = KnowledgeCollection.objects.get(pk=collection_id)
        except KnowledgeCollection.DoesNotExist:
            raise CommandError(f"Coleção não encontrada: {collection_id}")
        if not collection.is_active:
            raise CommandError(f"Coleção '{collection.name}' está inativa.")

        # --- Valida diretório ---
        source_dir = pathlib.Path(directory).resolve()
        if not source_dir.exists():
            raise CommandError(f"Diretório não encontrado: {source_dir}")
        if not source_dir.is_dir():
            raise CommandError(f"O caminho não é um diretório: {source_dir}")

        # --- Valida extensões ---
        accepted_exts = set(extensions) if extensions else SUPPORTED_EXTENSIONS
        unsupported = accepted_exts - SUPPORTED_EXTENSIONS
        if unsupported:
            raise CommandError(
                f"Extensão(ões) não suportada(s): {', '.join(sorted(unsupported))}. "
                f"Suportadas: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
            )

        # --- Opcional: usuário que dispara o job ---
        triggered_by = None
        if username:
            from apps.accounts.models import CustomUser
            try:
                triggered_by = CustomUser.objects.get(username=username)
            except CustomUser.DoesNotExist:
                raise CommandError(f"Usuário não encontrado: {username}")

        # --- Varredura de arquivos ---
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nBase de conhecimento: '{collection.name}'"
        ))
        self.stdout.write(f"  Diretório : {source_dir}")
        self.stdout.write(f"  Extensões : {', '.join(sorted(accepted_exts))}")
        self.stdout.write(f"  Recursivo : {'Sim' if recursive else 'Não'}")
        self.stdout.write("")

        files = list(self._scan_directory(source_dir, accepted_exts, recursive))
        total_found = len(files)

        if total_found == 0:
            self.stdout.write(self.style.WARNING("Nenhum arquivo encontrado no diretório."))
            return

        # --- Skip existing ---
        if skip_existing:
            existing_paths = set(
                KnowledgeDocument.objects.filter(collection=collection)
                .values_list("file_path", flat=True)
            )
            before = len(files)
            files = [f for f in files if str(f) not in existing_paths]
            skipped = before - len(files)
            if skipped:
                self.stdout.write(
                    self.style.WARNING(f"  {skipped} arquivo(s) ignorado(s) (já existem na coleção).")
                )

        if not files:
            self.stdout.write(self.style.WARNING("Nenhum arquivo novo para importar."))
            return

        self.stdout.write(f"  Arquivos a importar: {len(files)} de {total_found} encontrados")

        # --- Dry run ---
        if dry_run:
            self.stdout.write(self.style.SUCCESS("\n[DRY RUN] Arquivos que seriam importados:"))
            for f in files:
                self.stdout.write(f"  {f}")
            self.stdout.write(f"\nTotal: {len(files)} arquivo(s). Nenhum registro criado.")
            return

        # --- Cria o BulkImportJob ---
        job = BulkImportJob.objects.create(
            collection=collection,
            source_directory=str(source_dir),
            recursive=recursive,
            file_extensions=list(accepted_exts),
            status=BulkImportJob.Status.RUNNING,
            total_files=len(files),
            triggered_by=triggered_by,
        )
        self.stdout.write(self.style.SUCCESS(f"\nJob criado: {job.id}"))

        # --- Processa em lotes ---
        indexed = 0
        failed = 0
        errors: list[tuple[pathlib.Path, str]] = []

        for batch_start in range(0, len(files), batch_size):
            batch = files[batch_start : batch_start + batch_size]
            batch_indexed, batch_failed, batch_errors = self._process_batch(
                batch=batch,
                collection=collection,
                job=job,
                triggered_by=triggered_by,
                sync=sync,
            )
            indexed += batch_indexed
            failed += batch_failed
            errors.extend(batch_errors)

            # Atualiza contadores no banco após cada lote
            BulkImportJob.objects.filter(pk=job.pk).update(
                indexed_files=indexed,
                failed_files=failed,
            )

            self.stdout.write(
                f"  Lote {batch_start // batch_size + 1}: "
                f"{batch_indexed} enfileirados, {batch_failed} falhas "
                f"({indexed + failed}/{len(files)})"
            )

        # --- Finaliza o job ---
        if failed == 0:
            final_status = BulkImportJob.Status.COMPLETED
        else:
            final_status = BulkImportJob.Status.COMPLETED_WITH_ERRORS

        BulkImportJob.objects.filter(pk=job.pk).update(
            status=final_status,
            indexed_files=indexed,
            failed_files=failed,
        )

        # --- Resumo final ---
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("Importação concluída"))
        self.stdout.write(f"  Job ID          : {job.id}")
        self.stdout.write(f"  Coleção         : {collection.name}")
        self.stdout.write(f"  Total encontrado: {total_found}")
        self.stdout.write(f"  Enfileirados    : {indexed}")
        self.stdout.write(f"  Falhas          : {failed}")
        self.stdout.write(f"  Status final    : {final_status}")

        if errors:
            self.stdout.write(self.style.ERROR("\nArquivos com falha:"))
            for path, msg in errors:
                self.stdout.write(self.style.ERROR(f"  {path}: {msg}"))

        self.stdout.write(self.style.SUCCESS("=" * 60))

    # ------------------------------------------------------------------
    # Varredura de diretório
    # ------------------------------------------------------------------

    def _scan_directory(
        self,
        directory: pathlib.Path,
        extensions: set[str],
        recursive: bool,
    ) -> Iterator[pathlib.Path]:
        """Percorre o diretório e gera caminhos de arquivos com extensão aceita."""
        pattern = "**/*" if recursive else "*"
        for path in sorted(directory.glob(pattern)):
            if path.is_file():
                ext = path.suffix.lstrip(".").lower()
                if ext in extensions:
                    yield path

    # ------------------------------------------------------------------
    # Processamento de um lote
    # ------------------------------------------------------------------

    def _process_batch(
        self,
        batch: list[pathlib.Path],
        collection,
        job,
        triggered_by,
        sync: bool,
    ) -> tuple[int, int, list[tuple[pathlib.Path, str]]]:
        """
        Cria KnowledgeDocuments para um lote de arquivos e enfileira indexação.

        Retorna (indexed, failed, errors).
        """
        from apps.core.tasks import index_document
        from apps.knowledge.models import KnowledgeDocument

        docs_to_create = []
        path_to_ext: dict[str, str] = {}

        for file_path in batch:
            ext = file_path.suffix.lstrip(".").lower()
            path_to_ext[str(file_path)] = ext
            docs_to_create.append(
                KnowledgeDocument(
                    collection=collection,
                    title=file_path.stem.replace("_", " ").replace("-", " "),
                    file_path=str(file_path),
                    file_type=ext,
                    status=KnowledgeDocument.Status.PENDING,
                    ingested_by=triggered_by,
                    bulk_import_job=job,
                )
            )

        indexed = 0
        failed = 0
        errors: list[tuple[pathlib.Path, str]] = []

        try:
            with transaction.atomic():
                created = KnowledgeDocument.objects.bulk_create(
                    docs_to_create, ignore_conflicts=False
                )
        except Exception as exc:
            # Se o bulk_create falhar inteiro, tenta um a um para isolar falhas
            logger.warning("bulk_create falhou (%s), tentando um a um.", exc)
            created = []
            for doc in docs_to_create:
                try:
                    with transaction.atomic():
                        doc.save()
                    created.append(doc)
                except Exception as exc_single:
                    failed += 1
                    errors.append((pathlib.Path(doc.file_path), str(exc_single)))
                    logger.error("Falha ao criar documento para %s: %s", doc.file_path, exc_single)

        # Enfileira indexação para os documentos criados
        for doc in created:
            try:
                if sync:
                    index_document(str(doc.id), "knowledge")
                else:
                    task = index_document.delay(str(doc.id), "knowledge")
                    # Armazena o celery_task_id se precisar rastrear individualmente
                    logger.debug("Indexação enfileirada: doc=%s task=%s", doc.id, task.id)
                indexed += 1
            except Exception as exc:
                failed += 1
                errors.append((pathlib.Path(doc.file_path), f"Falha ao enfileirar indexação: {exc}"))
                logger.error("Falha ao enfileirar indexação para doc %s: %s", doc.id, exc)
                # Remove o documento para evitar estado órfão
                try:
                    doc.delete()
                    indexed -= 1
                except Exception:
                    pass

        return indexed, failed, errors
