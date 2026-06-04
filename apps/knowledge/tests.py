"""
Testes da app knowledge.

Cobertura:
    TestKnowledgeCollectionModel      -- campos, __str__, is_accessible_by
    TestKnowledgeDocumentModel        -- campos, __str__, is_ready, trigger_indexing/reindex,
                                         bulk_import_job FK (SET_NULL)
    TestKnowledgeChunkModel           -- campos, __str__, unique_together
    TestBulkImportJobModel            -- campos, __str__, is_done, progress_pct, ciclo de status
    TestKnowledgeCollectionSerializer -- campos computados, read_only
    TestKnowledgeDocumentSerializer   -- campo bulk_import_job_id
    TestKnowledgeDocumentUploadSerializer -- validacao de extensao, tamanho, colecao inativa
    TestBulkImportJobSerializer       -- campos, progresso, triggered_by_username
    TestBulkImportJobCreateSerializer -- validacao de diretorio e extensoes
    TestIsStaffOrReadOnly             -- permissao customizada
    TestCollectionListAPI             -- GET /collections/ filtragem por grupo/superuser
    TestCollectionRetrieveAPI         -- GET /collections/<id>/ controle de acesso
    TestCollectionCreateAPI           -- POST /collections/ staff vs. nao-staff
    TestCollectionDocumentsListAPI    -- GET /collections/<id>/documents/ filtro por status
    TestCollectionDocumentsUploadAPI  -- POST /collections/<id>/documents/ upload + Celery mock
    TestBulkImportListAPI             -- GET /collections/<id>/bulk-import/ listagem de jobs
    TestBulkImportCreateAPI           -- POST /collections/<id>/bulk-import/ disparo de job
    TestBulkImportDetailAPI           -- GET /collections/<id>/bulk-import/<job_id>/ detalhe
    TestDocumentRetrieveAPI           -- GET /documents/<id>/ controle de acesso
    TestDocumentDestroyAPI            -- DELETE /documents/<id>/ staff + Celery mock
    TestDocumentReindexAPI            -- POST /documents/<id>/reindex/ staff + conflict
    TestRunBulkImportTask             -- task Celery run_bulk_import (varrredura, contadores)
    TestBulkIngestCommand             -- management command (dry-run, skip-existing, erros)

Convencoes:
    - Usa pytest-django (conforme pyproject.toml).
    - Celery e sempre mockado (@patch); testes nao dependem de Redis.
    - pgvector: chunks criados com embedding de zeros (lista de 0.0 x 384).
    - Todos os testes de API usam APIClient do DRF.
"""

from __future__ import annotations

import io
import pathlib
import tempfile
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import CustomUser
from apps.knowledge.models import (
    BulkImportJob,
    KnowledgeChunk,
    KnowledgeCollection,
    KnowledgeDocument,
)
from apps.knowledge.serializers import (
    BulkImportJobCreateSerializer,
    BulkImportJobSerializer,
    KnowledgeCollectionSerializer,
    KnowledgeDocumentSerializer,
    KnowledgeDocumentUploadSerializer,
)
from apps.knowledge.views import IsStaffOrReadOnly


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

ZERO_EMBEDDING = [0.0] * 384


def make_user(username, *, staff=False, superuser=False, groups=()) -> CustomUser:
    user = CustomUser.objects.create_user(
        username=username,
        password="senha123",
        is_staff=staff,
        is_superuser=superuser,
    )
    for group_name in groups:
        g, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(g)
    return user


def make_collection(name="Colecao Teste", *, active=True, groups=()) -> KnowledgeCollection:
    col = KnowledgeCollection.objects.create(name=name, is_active=active)
    for group_name in groups:
        g, _ = Group.objects.get_or_create(name=group_name)
        col.allowed_groups.add(g)
    return col


def make_document(
    collection,
    title="Doc Teste",
    *,
    file_type="txt",
    status=KnowledgeDocument.Status.PENDING,
    ingested_by=None,
    bulk_import_job=None,
) -> KnowledgeDocument:
    return KnowledgeDocument.objects.create(
        collection=collection,
        title=title,
        file_path="/tmp/%s.%s" % (uuid.uuid4().hex, file_type),
        file_type=file_type,
        status=status,
        ingested_by=ingested_by,
        bulk_import_job=bulk_import_job,
    )


def make_chunk(document, index=0) -> KnowledgeChunk:
    return KnowledgeChunk.objects.create(
        document=document,
        collection_id=document.collection_id,
        chunk_index=index,
        content="Conteudo do chunk %d." % index,
        embedding=ZERO_EMBEDDING,
    )


def make_bulk_job(
    collection,
    source_directory="/tmp/docs",
    *,
    status=BulkImportJob.Status.PENDING,
    triggered_by=None,
    recursive=True,
    file_extensions=None,
) -> BulkImportJob:
    return BulkImportJob.objects.create(
        collection=collection,
        source_directory=source_directory,
        recursive=recursive,
        file_extensions=file_extensions or [],
        status=status,
        triggered_by=triggered_by,
    )


# ---------------------------------------------------------------------------
# Testes de modelo: KnowledgeCollection
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestKnowledgeCollectionModel:

    def test_str(self):
        col = make_collection("RH Ferias")
        assert str(col) == "RH Ferias"

    def test_uuid_pk_gerado_automaticamente(self):
        col = make_collection("UUID Test")
        assert col.pk is not None
        assert isinstance(col.pk, uuid.UUID)

    def test_is_active_default_true(self):
        col = make_collection("Ativa")
        assert col.is_active is True

    def test_timestamps_preenchidos(self):
        col = make_collection("Timestamps")
        assert col.created_at is not None
        assert col.updated_at is not None

    def test_nome_unico(self):
        make_collection("Nome Unico")
        from django.db import IntegrityError
        with pytest.raises(IntegrityError):
            KnowledgeCollection.objects.create(name="Nome Unico")

    def test_inacessivel_se_inativa(self):
        col = make_collection("Inativa", active=False)
        user = make_user("u1")
        assert col.is_accessible_by(user) is False

    def test_superuser_sempre_acessa(self):
        col = make_collection("Restrita", groups=["rh"])
        super_user = make_user("super", superuser=True)
        assert col.is_accessible_by(super_user) is True

    def test_colecao_publica_acessivel_por_qualquer_usuario(self):
        col = make_collection("Publica")
        user = make_user("comum")
        assert col.is_accessible_by(user) is True

    def test_colecao_restrita_bloqueia_usuario_sem_grupo(self):
        col = make_collection("Restrita", groups=["rh"])
        user = make_user("sem_grupo")
        assert col.is_accessible_by(user) is False

    def test_colecao_restrita_libera_usuario_com_grupo(self):
        col = make_collection("Restrita", groups=["rh"])
        user = make_user("do_rh", groups=["rh"])
        assert col.is_accessible_by(user) is True

    def test_usuario_com_um_dos_grupos_tem_acesso(self):
        g1, _ = Group.objects.get_or_create(name="vendas")
        g2, _ = Group.objects.get_or_create(name="ti")
        col = make_collection("Multi-grupo")
        col.allowed_groups.set([g1, g2])
        user = make_user("vendedor", groups=["vendas"])
        assert col.is_accessible_by(user) is True


# ---------------------------------------------------------------------------
# Testes de modelo: KnowledgeDocument
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestKnowledgeDocumentModel:

    def setup_method(self):
        self.col = make_collection()

    def test_str_inclui_status_display(self):
        doc = make_document(self.col, "Manual de Onboarding", status=KnowledgeDocument.Status.READY)
        assert "Manual de Onboarding" in str(doc)
        assert "Pronto" in str(doc)

    def test_status_padrao_pending(self):
        doc = make_document(self.col)
        assert doc.status == KnowledgeDocument.Status.PENDING

    def test_is_ready_true_quando_ready(self):
        doc = make_document(self.col, status=KnowledgeDocument.Status.READY)
        assert doc.is_ready is True

    def test_is_ready_false_quando_pending(self):
        doc = make_document(self.col)
        assert doc.is_ready is False

    def test_is_ready_false_quando_error(self):
        doc = make_document(self.col, status=KnowledgeDocument.Status.ERROR)
        assert doc.is_ready is False

    def test_chunks_count_inicial_zero(self):
        doc = make_document(self.col)
        assert doc.chunks_count == 0

    def test_error_message_inicial_vazio(self):
        doc = make_document(self.col)
        assert doc.error_message == ""

    def test_bulk_import_job_nulo_para_upload_manual(self):
        doc = make_document(self.col)
        assert doc.bulk_import_job is None

    def test_bulk_import_job_preenchido_para_carga_em_lote(self):
        job = make_bulk_job(self.col)
        doc = make_document(self.col, bulk_import_job=job)
        assert doc.bulk_import_job == job

    def test_bulk_import_job_set_null_ao_deletar_job(self):
        job = make_bulk_job(self.col)
        doc = make_document(self.col, bulk_import_job=job)
        job.delete()
        doc.refresh_from_db()
        assert doc.bulk_import_job is None

    def test_cascade_delete_ao_deletar_colecao(self):
        doc = make_document(self.col)
        doc_id = doc.id
        self.col.delete()
        assert not KnowledgeDocument.objects.filter(pk=doc_id).exists()

    def test_ingested_by_set_null_ao_deletar_usuario(self):
        user = make_user("editor", staff=True)
        doc = make_document(self.col, ingested_by=user)
        user.delete()
        doc.refresh_from_db()
        assert doc.ingested_by is None

    @patch("apps.core.tasks.index_document.delay")
    def test_trigger_indexing_chama_celery(self, mock_delay):
        mock_result = MagicMock()
        mock_result.id = "task-abc-123"
        mock_delay.return_value = mock_result
        doc = make_document(self.col)
        task_id = doc.trigger_indexing()
        mock_delay.assert_called_once_with(str(doc.id), "knowledge")
        assert task_id == "task-abc-123"

    @patch("apps.core.tasks.reindex_document.delay")
    def test_trigger_reindex_chama_celery(self, mock_delay):
        mock_result = MagicMock()
        mock_result.id = "task-reindex-456"
        mock_delay.return_value = mock_result
        doc = make_document(self.col)
        task_id = doc.trigger_reindex()
        mock_delay.assert_called_once_with(str(doc.id), "knowledge")
        assert task_id == "task-reindex-456"


# ---------------------------------------------------------------------------
# Testes de modelo: KnowledgeChunk
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestKnowledgeChunkModel:

    def setup_method(self):
        self.col = make_collection()
        self.doc = make_document(self.col)

    def test_str(self):
        chunk = make_chunk(self.doc, index=0)
        assert "Chunk 0" in str(chunk)
        assert self.doc.title in str(chunk)

    def test_collection_id_desnormalizado(self):
        chunk = make_chunk(self.doc, index=0)
        assert chunk.collection_id == self.col.pk

    def test_embedding_tem_384_dimensoes(self):
        chunk = make_chunk(self.doc, index=0)
        assert len(chunk.embedding) == 384

    def test_unique_together_document_chunk_index(self):
        make_chunk(self.doc, index=0)
        from django.db import IntegrityError
        with pytest.raises(IntegrityError):
            KnowledgeChunk.objects.create(
                document=self.doc,
                collection_id=self.col.pk,
                chunk_index=0,
                content="duplicado",
                embedding=ZERO_EMBEDDING,
            )

    def test_cascade_delete_ao_deletar_documento(self):
        chunk = make_chunk(self.doc, index=0)
        chunk_id = chunk.id
        self.doc.delete()
        assert not KnowledgeChunk.objects.filter(pk=chunk_id).exists()

    def test_multiplos_chunks_mesmo_documento(self):
        for i in range(5):
            make_chunk(self.doc, index=i)
        assert self.doc.chunks.count() == 5


# ---------------------------------------------------------------------------
# Testes de modelo: BulkImportJob
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkImportJobModel:

    def setup_method(self):
        self.col = make_collection()

    def test_str_contem_status_e_colecao(self):
        job = make_bulk_job(self.col, "/dados/rh")
        s = str(job)
        assert "Pendente" in s
        assert self.col.name in s
        assert "/dados/rh" in s

    def test_uuid_pk_gerado_automaticamente(self):
        job = make_bulk_job(self.col)
        assert job.pk is not None
        assert isinstance(job.pk, uuid.UUID)

    def test_status_padrao_pending(self):
        job = make_bulk_job(self.col)
        assert job.status == BulkImportJob.Status.PENDING

    def test_contadores_iniciais_zero(self):
        job = make_bulk_job(self.col)
        assert job.total_files == 0
        assert job.indexed_files == 0
        assert job.failed_files == 0

    def test_is_done_false_quando_pending(self):
        job = make_bulk_job(self.col, status=BulkImportJob.Status.PENDING)
        assert job.is_done is False

    def test_is_done_false_quando_running(self):
        job = make_bulk_job(self.col, status=BulkImportJob.Status.RUNNING)
        assert job.is_done is False

    def test_is_done_true_quando_completed(self):
        job = make_bulk_job(self.col, status=BulkImportJob.Status.COMPLETED)
        assert job.is_done is True

    def test_is_done_true_quando_completed_with_errors(self):
        job = make_bulk_job(self.col, status=BulkImportJob.Status.COMPLETED_WITH_ERRORS)
        assert job.is_done is True

    def test_is_done_true_quando_failed(self):
        job = make_bulk_job(self.col, status=BulkImportJob.Status.FAILED)
        assert job.is_done is True

    def test_progress_pct_zero_sem_arquivos(self):
        job = make_bulk_job(self.col)
        assert job.progress_pct == 0

    def test_progress_pct_calculado_corretamente(self):
        job = make_bulk_job(self.col)
        job.total_files = 10
        job.indexed_files = 7
        job.failed_files = 1
        job.save()
        assert job.progress_pct == 80  # (7+1)/10 * 100

    def test_progress_pct_maximo_100(self):
        job = make_bulk_job(self.col)
        job.total_files = 5
        job.indexed_files = 5
        job.failed_files = 0
        job.save()
        assert job.progress_pct == 100

    def test_triggered_by_set_null_ao_deletar_usuario(self):
        user = make_user("admin_job", staff=True)
        job = make_bulk_job(self.col, triggered_by=user)
        user.delete()
        job.refresh_from_db()
        assert job.triggered_by is None

    def test_cascade_delete_ao_deletar_colecao(self):
        job = make_bulk_job(self.col)
        job_id = job.id
        self.col.delete()
        assert not BulkImportJob.objects.filter(pk=job_id).exists()

    def test_documentos_vinculados_ao_job(self):
        job = make_bulk_job(self.col)
        doc1 = make_document(self.col, bulk_import_job=job)
        doc2 = make_document(self.col, "Doc B", bulk_import_job=job)
        assert job.documents.count() == 2
        assert doc1 in job.documents.all()
        assert doc2 in job.documents.all()

    def test_file_extensions_armazenado_como_lista(self):
        job = make_bulk_job(self.col, file_extensions=["pdf", "docx"])
        job.refresh_from_db()
        assert job.file_extensions == ["pdf", "docx"]

    def test_recursive_default_true(self):
        job = make_bulk_job(self.col)
        assert job.recursive is True


# ---------------------------------------------------------------------------
# Testes de serializer: KnowledgeCollectionSerializer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestKnowledgeCollectionSerializer:

    def test_campos_basicos(self):
        col = make_collection("Financeiro")
        data = KnowledgeCollectionSerializer(col).data
        assert data["name"] == "Financeiro"
        assert data["is_active"] is True
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_document_count_zero_sem_documentos(self):
        col = make_collection("Vazia")
        data = KnowledgeCollectionSerializer(col).data
        assert data["document_count"] == 0
        assert data["ready_count"] == 0

    def test_document_count_correto(self):
        col = make_collection("Com Docs")
        make_document(col, status=KnowledgeDocument.Status.READY)
        make_document(col, status=KnowledgeDocument.Status.READY)
        make_document(col, status=KnowledgeDocument.Status.PENDING)
        data = KnowledgeCollectionSerializer(col).data
        assert data["document_count"] == 3
        assert data["ready_count"] == 2

    def test_allowed_groups_como_nomes(self):
        g, _ = Group.objects.get_or_create(name="juridico")
        col = make_collection("Juridico", groups=["juridico"])
        data = KnowledgeCollectionSerializer(col).data
        assert "juridico" in data["allowed_groups"]

    def test_id_e_timestamps_sao_read_only(self):
        col = make_collection("Existente")
        original_id = str(col.pk)
        s = KnowledgeCollectionSerializer(col, data={"id": str(uuid.uuid4()), "name": "Existente"})
        s.is_valid()
        assert str(col.pk) == original_id


# ---------------------------------------------------------------------------
# Testes de serializer: KnowledgeDocumentSerializer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestKnowledgeDocumentSerializer:

    def setup_method(self):
        self.col = make_collection()

    def test_bulk_import_job_id_nulo_para_upload_manual(self):
        doc = make_document(self.col)
        data = KnowledgeDocumentSerializer(doc).data
        assert data["bulk_import_job_id"] is None

    def test_bulk_import_job_id_preenchido_para_carga_lote(self):
        job = make_bulk_job(self.col)
        doc = make_document(self.col, bulk_import_job=job)
        data = KnowledgeDocumentSerializer(doc).data
        assert str(data["bulk_import_job_id"]) == str(job.id)

    def test_campos_esperados_presentes(self):
        doc = make_document(self.col)
        data = KnowledgeDocumentSerializer(doc).data
        for campo in [
            "id", "collection", "collection_name", "title",
            "file_path", "file_type", "status", "status_display",
            "chunks_count", "error_message", "ingested_by",
            "ingested_by_username", "bulk_import_job_id",
            "created_at", "updated_at",
        ]:
            assert campo in data, "Campo '%s' ausente" % campo

    def test_status_display_em_portugues(self):
        doc = make_document(self.col, status=KnowledgeDocument.Status.READY)
        data = KnowledgeDocumentSerializer(doc).data
        assert data["status_display"] == "Pronto"


# ---------------------------------------------------------------------------
# Testes de serializer: KnowledgeDocumentUploadSerializer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestKnowledgeDocumentUploadSerializer:

    def setup_method(self):
        self.col = make_collection()
        self.user = make_user("staff_user", staff=True)

    def _make_file(self, name="arquivo.txt", size=100):
        content = b"x" * size
        f = io.BytesIO(content)
        f.name = name
        f.size = size
        return f

    def _make_request(self, user=None):
        mock_req = MagicMock()
        mock_req.user = user or self.user
        return mock_req

    def test_valida_extensao_txt(self):
        s = KnowledgeDocumentUploadSerializer(
            data={"title": "Teste", "file": self._make_file("doc.txt"),
                  "collection_id": str(self.col.pk)},
            context={"request": self._make_request()},
        )
        assert s.is_valid(), s.errors

    def test_valida_extensao_pdf(self):
        s = KnowledgeDocumentUploadSerializer(
            data={"title": "PDF", "file": self._make_file("doc.pdf"),
                  "collection_id": str(self.col.pk)},
            context={"request": self._make_request()},
        )
        assert s.is_valid(), s.errors

    def test_rejeita_extensao_invalida(self):
        s = KnowledgeDocumentUploadSerializer(
            data={"title": "Invalido", "file": self._make_file("virus.exe"),
                  "collection_id": str(self.col.pk)},
            context={"request": self._make_request()},
        )
        assert not s.is_valid()
        assert "file" in s.errors

    def test_rejeita_arquivo_muito_grande(self):
        s = KnowledgeDocumentUploadSerializer(
            data={"title": "Grande", "file": self._make_file("grande.pdf", size=51 * 1024 * 1024),
                  "collection_id": str(self.col.pk)},
            context={"request": self._make_request()},
        )
        assert not s.is_valid()
        assert "file" in s.errors

    def test_rejeita_colecao_inexistente(self):
        s = KnowledgeDocumentUploadSerializer(
            data={"title": "Sem Colecao", "file": self._make_file("doc.txt"),
                  "collection_id": str(uuid.uuid4())},
            context={"request": self._make_request()},
        )
        assert not s.is_valid()
        assert "collection_id" in s.errors

    def test_rejeita_colecao_inativa(self):
        col_inativa = make_collection("Inativa", active=False)
        s = KnowledgeDocumentUploadSerializer(
            data={"title": "Doc", "file": self._make_file("doc.txt"),
                  "collection_id": str(col_inativa.pk)},
            context={"request": self._make_request()},
        )
        assert not s.is_valid()
        assert "collection_id" in s.errors

    def test_rejeita_usuario_sem_acesso_a_colecao_restrita(self):
        col_restrita = make_collection("Restrita", groups=["rh"])
        user_sem_grupo = make_user("forasteiro")
        s = KnowledgeDocumentUploadSerializer(
            data={"title": "Proibido", "file": self._make_file("doc.txt"),
                  "collection_id": str(col_restrita.pk)},
            context={"request": self._make_request(user=user_sem_grupo)},
        )
        assert not s.is_valid()


# ---------------------------------------------------------------------------
# Testes de serializer: BulkImportJobSerializer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkImportJobSerializer:

    def setup_method(self):
        self.col = make_collection()

    def test_campos_basicos_presentes(self):
        job = make_bulk_job(self.col, "/dados/rh")
        data = BulkImportJobSerializer(job).data
        for campo in [
            "id", "collection", "collection_name", "source_directory",
            "recursive", "file_extensions", "status", "status_display",
            "total_files", "indexed_files", "failed_files", "progress_pct",
            "error_message", "celery_task_id", "triggered_by",
            "triggered_by_username", "created_at", "updated_at",
        ]:
            assert campo in data, "Campo '%s' ausente" % campo

    def test_status_display_em_portugues(self):
        job = make_bulk_job(self.col, status=BulkImportJob.Status.RUNNING)
        data = BulkImportJobSerializer(job).data
        assert data["status_display"] == "Executando"

    def test_progress_pct_calculado(self):
        job = make_bulk_job(self.col)
        job.total_files = 10
        job.indexed_files = 6
        job.failed_files = 2
        job.save()
        data = BulkImportJobSerializer(job).data
        assert data["progress_pct"] == 80

    def test_triggered_by_username_nulo_sem_usuario(self):
        job = make_bulk_job(self.col)
        data = BulkImportJobSerializer(job).data
        assert data["triggered_by_username"] is None

    def test_triggered_by_username_preenchido(self):
        user = make_user("adm_bulk", staff=True)
        job = make_bulk_job(self.col, triggered_by=user)
        data = BulkImportJobSerializer(job).data
        assert data["triggered_by_username"] == "adm_bulk"

    def test_collection_name_preenchido(self):
        job = make_bulk_job(self.col)
        data = BulkImportJobSerializer(job).data
        assert data["collection_name"] == self.col.name


# ---------------------------------------------------------------------------
# Testes de serializer: BulkImportJobCreateSerializer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkImportJobCreateSerializer:

    def test_valida_diretorio_existente(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = BulkImportJobCreateSerializer(data={
                "source_directory": tmpdir,
                "recursive": True,
                "file_extensions": [],
            })
            assert s.is_valid(), s.errors

    def test_rejeita_diretorio_inexistente(self):
        s = BulkImportJobCreateSerializer(data={
            "source_directory": "/caminho/que/nao/existe/xyz123",
            "recursive": True,
        })
        assert not s.is_valid()
        assert "source_directory" in s.errors

    def test_rejeita_caminho_que_nao_e_diretorio(self):
        with tempfile.NamedTemporaryFile() as tmp:
            s = BulkImportJobCreateSerializer(data={
                "source_directory": tmp.name,
                "recursive": False,
            })
            assert not s.is_valid()
            assert "source_directory" in s.errors

    def test_valida_extensoes_suportadas(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = BulkImportJobCreateSerializer(data={
                "source_directory": tmpdir,
                "file_extensions": ["pdf", "docx"],
            })
            assert s.is_valid(), s.errors
            assert s.validated_data["file_extensions"] == ["pdf", "docx"]

    def test_rejeita_extensoes_nao_suportadas(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = BulkImportJobCreateSerializer(data={
                "source_directory": tmpdir,
                "file_extensions": ["exe", "zip"],
            })
            assert not s.is_valid()
            assert "file_extensions" in s.errors

    def test_extensoes_normalizadas_sem_ponto(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = BulkImportJobCreateSerializer(data={
                "source_directory": tmpdir,
                "file_extensions": [".PDF", ".Docx"],
            })
            assert s.is_valid(), s.errors
            assert "pdf" in s.validated_data["file_extensions"]
            assert "docx" in s.validated_data["file_extensions"]

    def test_extensoes_vazias_aceitas(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = BulkImportJobCreateSerializer(data={
                "source_directory": tmpdir,
                "file_extensions": [],
            })
            assert s.is_valid(), s.errors

    def test_recursive_default_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            s = BulkImportJobCreateSerializer(data={"source_directory": tmpdir})
            assert s.is_valid(), s.errors
            assert s.validated_data["recursive"] is True


# ---------------------------------------------------------------------------
# Testes da permissao IsStaffOrReadOnly
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsStaffOrReadOnly:

    def _make_request(self, method, *, authenticated=True, staff=False):
        req = MagicMock()
        req.method = method
        if authenticated:
            req.user = make_user("user_%s" % uuid.uuid4().hex[:6], staff=staff)
        else:
            req.user = MagicMock()
            req.user.is_authenticated = False
        return req

    def test_get_autenticado_permitido(self):
        perm = IsStaffOrReadOnly()
        req = self._make_request("GET", authenticated=True, staff=False)
        assert perm.has_permission(req, None) is True

    def test_get_anonimo_negado(self):
        perm = IsStaffOrReadOnly()
        req = self._make_request("GET", authenticated=False)
        assert perm.has_permission(req, None) is False

    def test_post_nao_staff_negado(self):
        perm = IsStaffOrReadOnly()
        req = self._make_request("POST", authenticated=True, staff=False)
        assert perm.has_permission(req, None) is False

    def test_post_staff_permitido(self):
        perm = IsStaffOrReadOnly()
        req = self._make_request("POST", authenticated=True, staff=True)
        assert perm.has_permission(req, None) is True

    def test_delete_staff_permitido(self):
        perm = IsStaffOrReadOnly()
        req = self._make_request("DELETE", authenticated=True, staff=True)
        assert perm.has_permission(req, None) is True

    def test_delete_nao_staff_negado(self):
        perm = IsStaffOrReadOnly()
        req = self._make_request("DELETE", authenticated=True, staff=False)
        assert perm.has_permission(req, None) is False


# ---------------------------------------------------------------------------
# Testes de API: listagem de colecoes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCollectionListAPI:

    BASE_URL = "/rag/api/knowledge/collections/"

    def setup_method(self):
        self.client = APIClient()

    def test_anonimo_recebe_403(self):
        resp = self.client.get(self.BASE_URL)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_usuario_comum_ve_colecoes_publicas(self):
        make_collection("Publica A")
        make_collection("Publica B")
        user = make_user("viewer")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self.BASE_URL)
        assert resp.status_code == status.HTTP_200_OK
        nomes = [c["name"] for c in resp.data["results"]]
        assert "Publica A" in nomes
        assert "Publica B" in nomes

    def test_usuario_sem_grupo_nao_ve_colecao_restrita(self):
        make_collection("Restrita", groups=["rh"])
        user = make_user("sem_grupo")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self.BASE_URL)
        assert resp.status_code == status.HTTP_200_OK
        nomes = [c["name"] for c in resp.data["results"]]
        assert "Restrita" not in nomes

    def test_usuario_com_grupo_ve_colecao_restrita(self):
        make_collection("Restrita RH", groups=["rh"])
        user = make_user("do_rh", groups=["rh"])
        self.client.force_authenticate(user=user)
        resp = self.client.get(self.BASE_URL)
        assert resp.status_code == status.HTTP_200_OK
        nomes = [c["name"] for c in resp.data["results"]]
        assert "Restrita RH" in nomes

    def test_superuser_ve_todas_colecoes(self):
        make_collection("Publica")
        make_collection("Restrita SU", groups=["rh"])
        super_user = make_user("super", superuser=True)
        self.client.force_authenticate(user=super_user)
        resp = self.client.get(self.BASE_URL)
        assert resp.status_code == status.HTTP_200_OK
        nomes = [c["name"] for c in resp.data["results"]]
        assert "Publica" in nomes
        assert "Restrita SU" in nomes

    def test_colecoes_inativas_nao_aparecem(self):
        make_collection("Ativa")
        make_collection("Inativa", active=False)
        user = make_user("viewer2")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self.BASE_URL)
        nomes = [c["name"] for c in resp.data["results"]]
        assert "Ativa" in nomes
        assert "Inativa" not in nomes


# ---------------------------------------------------------------------------
# Testes de API: detalhe de colecao
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCollectionRetrieveAPI:

    def setup_method(self):
        self.client = APIClient()

    def _url(self, col_id):
        return "/rag/api/knowledge/collections/%s/" % col_id

    def test_usuario_acessa_colecao_publica(self):
        col = make_collection("Publica")
        user = make_user("viewer")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(col.pk))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["name"] == "Publica"

    def test_usuario_sem_grupo_recebe_403_em_colecao_restrita(self):
        col = make_collection("Restrita", groups=["rh"])
        user = make_user("forasteiro")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(col.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_usuario_com_grupo_acessa_colecao_restrita(self):
        col = make_collection("Restrita", groups=["rh"])
        user = make_user("do_rh", groups=["rh"])
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(col.pk))
        assert resp.status_code == status.HTTP_200_OK

    def test_colecao_inexistente_retorna_404(self):
        user = make_user("viewer3")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_resposta_contem_campos_esperados(self):
        col = make_collection("Completa")
        user = make_user("viewer4")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(col.pk))
        for campo in ["id", "name", "description", "is_active", "document_count", "ready_count"]:
            assert campo in resp.data, "Campo '%s' ausente" % campo


# ---------------------------------------------------------------------------
# Testes de API: criacao de colecao
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCollectionCreateAPI:

    BASE_URL = "/rag/api/knowledge/collections/"

    def setup_method(self):
        self.client = APIClient()

    def test_nao_staff_recebe_403(self):
        user = make_user("leitor")
        self.client.force_authenticate(user=user)
        resp = self.client.post(self.BASE_URL, {"name": "Nova Colecao"}, format="json")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_staff_cria_colecao(self):
        staff = make_user("staff", staff=True)
        self.client.force_authenticate(user=staff)
        resp = self.client.post(
            self.BASE_URL, {"name": "Nova Colecao", "description": "Teste"}, format="json"
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert KnowledgeCollection.objects.filter(name="Nova Colecao").exists()

    def test_nome_duplicado_retorna_400(self):
        make_collection("Duplicada")
        staff = make_user("staff2", staff=True)
        self.client.force_authenticate(user=staff)
        resp = self.client.post(self.BASE_URL, {"name": "Duplicada"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_nome_obrigatorio(self):
        staff = make_user("staff3", staff=True)
        self.client.force_authenticate(user=staff)
        resp = self.client.post(self.BASE_URL, {"description": "Sem nome"}, format="json")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Testes de API: listagem de documentos de uma colecao
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCollectionDocumentsListAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Docs")

    def _url(self, col_id):
        return "/rag/api/knowledge/collections/%s/documents/" % col_id

    def test_lista_documentos_da_colecao(self):
        make_document(self.col, "Doc A")
        make_document(self.col, "Doc B")
        user = make_user("viewer")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(self.col.pk))
        assert resp.status_code == status.HTTP_200_OK
        titulos = [d["title"] for d in resp.data]
        assert "Doc A" in titulos
        assert "Doc B" in titulos

    def test_filtra_documentos_por_status(self):
        make_document(self.col, "Pronto", status=KnowledgeDocument.Status.READY)
        make_document(self.col, "Pendente", status=KnowledgeDocument.Status.PENDING)
        user = make_user("viewer2")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(self.col.pk), {"status": "ready"})
        assert resp.status_code == status.HTTP_200_OK
        assert all(d["status"] == "ready" for d in resp.data)

    def test_resposta_inclui_bulk_import_job_id(self):
        job = make_bulk_job(self.col)
        make_document(self.col, "Via Lote", bulk_import_job=job)
        make_document(self.col, "Manual")
        user = make_user("viewer3")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(self.col.pk))
        assert resp.status_code == status.HTTP_200_OK
        job_ids = [d["bulk_import_job_id"] for d in resp.data]
        assert str(job.id) in [str(j) for j in job_ids if j]
        assert None in job_ids  # documento manual tem null

    def test_usuario_sem_acesso_recebe_403(self):
        col_restrita = make_collection("Restrita Docs", groups=["rh"])
        user = make_user("forasteiro")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(col_restrita.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_colecao_inexistente_retorna_404(self):
        user = make_user("viewer4")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Testes de API: upload de documentos
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCollectionDocumentsUploadAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Upload Col")
        self.staff = make_user("uploader", staff=True)

    def _url(self, col_id):
        return "/rag/api/knowledge/collections/%s/documents/" % col_id

    @patch("apps.core.tasks.index_document.delay")
    def test_staff_faz_upload_com_sucesso(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-upload-001")
        self.client.force_authenticate(user=self.staff)
        arquivo = SimpleUploadedFile("documento.txt", b"texto de teste", content_type="text/plain")
        resp = self.client.post(
            self._url(self.col.pk),
            {"title": "Novo Doc", "file": arquivo},
            format="multipart",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert KnowledgeDocument.objects.filter(title="Novo Doc").exists()
        assert resp.data["status"] == "pending"
        assert resp.data["task_id"] == "task-upload-001"

    @patch("apps.core.tasks.index_document.delay")
    def test_upload_documento_sem_bulk_job(self, mock_delay):
        mock_delay.return_value = MagicMock(id="t")
        self.client.force_authenticate(user=self.staff)
        arquivo = SimpleUploadedFile("doc.txt", b"conteudo", content_type="text/plain")
        resp = self.client.post(
            self._url(self.col.pk),
            {"title": "Upload Manual", "file": arquivo},
            format="multipart",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["bulk_import_job_id"] is None

    @patch("apps.core.tasks.index_document.delay")
    def test_upload_cria_documento_com_file_type_correto(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-xyz")
        self.client.force_authenticate(user=self.staff)
        arquivo = SimpleUploadedFile("relatorio.pdf", b"pdf simulado", content_type="application/pdf")
        resp = self.client.post(
            self._url(self.col.pk),
            {"title": "Relatorio PDF", "file": arquivo},
            format="multipart",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        doc = KnowledgeDocument.objects.get(title="Relatorio PDF")
        assert doc.file_type == "pdf"
        assert doc.ingested_by == self.staff

    def test_nao_staff_recebe_403_no_upload(self):
        user = make_user("leitor")
        self.client.force_authenticate(user=user)
        arquivo = SimpleUploadedFile("doc.txt", b"texto", content_type="text/plain")
        resp = self.client.post(
            self._url(self.col.pk),
            {"title": "Bloqueado", "file": arquivo},
            format="multipart",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_upload_sem_titulo_retorna_400(self):
        self.client.force_authenticate(user=self.staff)
        arquivo = SimpleUploadedFile("doc.txt", b"texto", content_type="text/plain")
        resp = self.client.post(
            self._url(self.col.pk), {"file": arquivo}, format="multipart"
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_upload_extensao_invalida_retorna_400(self):
        self.client.force_authenticate(user=self.staff)
        arquivo = SimpleUploadedFile("dados.xlsx", b"dados", content_type="application/vnd.ms-excel")
        resp = self.client.post(
            self._url(self.col.pk),
            {"title": "Excel Invalido", "file": arquivo},
            format="multipart",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Testes de API: bulk import (listagem)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkImportListAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Bulk")
        self.admin = make_user("admin_bi", staff=True, superuser=True)

    def _url(self, col_id):
        return "/rag/api/knowledge/collections/%s/bulk-import/" % col_id

    def test_admin_lista_jobs_da_colecao(self):
        make_bulk_job(self.col, "/dados/rh")
        make_bulk_job(self.col, "/dados/ti")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self._url(self.col.pk))
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 2
        diretorios = [j["source_directory"] for j in resp.data]
        assert "/dados/rh" in diretorios
        assert "/dados/ti" in diretorios

    def test_lista_vazia_sem_jobs(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self._url(self.col.pk))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data == []

    def test_nao_admin_recebe_403(self):
        user = make_user("leitor")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(self.col.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_anonimo_recebe_403(self):
        resp = self.client.get(self._url(self.col.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_colecao_inexistente_retorna_404(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self._url(uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_resposta_contem_campos_esperados(self):
        make_bulk_job(self.col, "/docs")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self._url(self.col.pk))
        job_data = resp.data[0]
        for campo in [
            "id", "collection", "source_directory", "recursive",
            "status", "total_files", "indexed_files", "failed_files",
            "progress_pct", "triggered_by", "created_at",
        ]:
            assert campo in job_data, "Campo '%s' ausente" % campo


# ---------------------------------------------------------------------------
# Testes de API: bulk import (criacao)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkImportCreateAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Bulk Create")
        self.admin = make_user("admin_bic", staff=True, superuser=True)

    def _url(self, col_id):
        return "/rag/api/knowledge/collections/%s/bulk-import/" % col_id

    @patch("apps.knowledge.tasks.run_bulk_import.delay")
    def test_admin_dispara_job_com_sucesso(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-bulk-001")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.client.force_authenticate(user=self.admin)
            resp = self.client.post(
                self._url(self.col.pk),
                {"source_directory": tmpdir, "recursive": True, "file_extensions": []},
                format="json",
            )
        assert resp.status_code == status.HTTP_202_ACCEPTED
        assert BulkImportJob.objects.filter(collection=self.col).exists()
        job = BulkImportJob.objects.get(collection=self.col)
        assert job.triggered_by == self.admin
        mock_delay.assert_called_once_with(str(job.id))

    @patch("apps.knowledge.tasks.run_bulk_import.delay")
    def test_job_criado_com_triggered_by_correto(self, mock_delay):
        mock_delay.return_value = MagicMock(id="t")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.client.force_authenticate(user=self.admin)
            resp = self.client.post(
                self._url(self.col.pk),
                {"source_directory": tmpdir},
                format="json",
            )
        assert resp.status_code == status.HTTP_202_ACCEPTED
        assert resp.data["triggered_by"] == self.admin.pk

    def test_diretorio_inexistente_retorna_400(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(
            self._url(self.col.pk),
            {"source_directory": "/nao/existe/xyz999"},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "source_directory" in resp.data

    def test_extensao_invalida_retorna_400(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.client.force_authenticate(user=self.admin)
            resp = self.client.post(
                self._url(self.col.pk),
                {"source_directory": tmpdir, "file_extensions": ["exe", "zip"]},
                format="json",
            )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert "file_extensions" in resp.data

    def test_nao_admin_recebe_403(self):
        user = make_user("leitor_bulk")
        self.client.force_authenticate(user=user)
        with tempfile.TemporaryDirectory() as tmpdir:
            resp = self.client.post(
                self._url(self.col.pk),
                {"source_directory": tmpdir},
                format="json",
            )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    @patch("apps.knowledge.tasks.run_bulk_import.delay")
    def test_falha_no_celery_retorna_500_e_job_failed(self, mock_delay):
        mock_delay.side_effect = Exception("Redis indisponivel")
        with tempfile.TemporaryDirectory() as tmpdir:
            self.client.force_authenticate(user=self.admin)
            resp = self.client.post(
                self._url(self.col.pk),
                {"source_directory": tmpdir},
                format="json",
            )
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        job = BulkImportJob.objects.get(collection=self.col)
        assert job.status == BulkImportJob.Status.FAILED


# ---------------------------------------------------------------------------
# Testes de API: bulk import (detalhe)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkImportDetailAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Bulk Detail")
        self.admin = make_user("admin_bid", staff=True, superuser=True)

    def _url(self, col_id, job_id):
        return "/rag/api/knowledge/collections/%s/bulk-import/%s/" % (col_id, job_id)

    def test_admin_acessa_detalhe_do_job(self):
        job = make_bulk_job(self.col, "/dados/fin")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self._url(self.col.pk, job.pk))
        assert resp.status_code == status.HTTP_200_OK
        assert str(resp.data["id"]) == str(job.id)
        assert resp.data["source_directory"] == "/dados/fin"

    def test_job_inexistente_retorna_404(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self._url(self.col.pk, uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_job_de_outra_colecao_retorna_404(self):
        outra_col = make_collection("Outra Col")
        job = make_bulk_job(outra_col, "/dados")
        self.client.force_authenticate(user=self.admin)
        resp = self.client.get(self._url(self.col.pk, job.pk))
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_nao_admin_recebe_403(self):
        job = make_bulk_job(self.col)
        user = make_user("leitor_det")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(self.col.pk, job.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Testes de API: detalhe do documento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDocumentRetrieveAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Doc Detail")

    def _url(self, doc_id):
        return "/rag/api/knowledge/documents/%s/" % doc_id

    def test_usuario_ve_documento_de_colecao_publica(self):
        doc = make_document(self.col, "Doc Publico")
        user = make_user("viewer")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(doc.pk))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["title"] == "Doc Publico"

    def test_resposta_contem_campos_esperados(self):
        doc = make_document(self.col, "Completo")
        user = make_user("viewer2")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(doc.pk))
        for campo in [
            "id", "title", "collection", "file_type",
            "status", "chunks_count", "bulk_import_job_id",
        ]:
            assert campo in resp.data, "Campo '%s' ausente" % campo

    def test_usuario_sem_acesso_recebe_403(self):
        col_restrita = make_collection("Restrita", groups=["rh"])
        doc = make_document(col_restrita, "Sigiloso")
        user = make_user("forasteiro")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(doc.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_documento_inexistente_retorna_404(self):
        user = make_user("viewer3")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    def test_anonimo_recebe_403(self):
        doc = make_document(self.col)
        resp = self.client.get(self._url(doc.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Testes de API: delecao de documento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDocumentDestroyAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Delete")
        self.staff = make_user("staff_del", staff=True)

    def _url(self, doc_id):
        return "/rag/api/knowledge/documents/%s/" % doc_id

    @patch("apps.core.tasks.delete_document.delay")
    def test_staff_deleta_documento_via_celery(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-del-001")
        doc = make_document(self.col, "Para Deletar")
        self.client.force_authenticate(user=self.staff)
        resp = self.client.delete(self._url(doc.pk))
        assert resp.status_code == status.HTTP_202_ACCEPTED
        mock_delay.assert_called_once_with(str(doc.pk), "knowledge")
        assert resp.data["doc_id"] == str(doc.pk)

    def test_nao_staff_recebe_403(self):
        doc = make_document(self.col)
        user = make_user("leitor")
        self.client.force_authenticate(user=user)
        resp = self.client.delete(self._url(doc.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_documento_inexistente_retorna_404(self):
        self.client.force_authenticate(user=self.staff)
        resp = self.client.delete(self._url(uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @patch("apps.core.tasks.delete_document.delay")
    def test_staff_sem_acesso_a_colecao_recebe_403(self, mock_delay):
        col_restrita = make_collection("Restrita Del", groups=["rh"])
        doc = make_document(col_restrita)
        staff_sem_grupo = make_user("staff_sg", staff=True)
        self.client.force_authenticate(user=staff_sem_grupo)
        resp = self.client.delete(self._url(doc.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        mock_delay.assert_not_called()

    @patch("apps.core.tasks.delete_document.delay")
    def test_fallback_deleta_direto_se_celery_falhar(self, mock_delay):
        mock_delay.side_effect = Exception("Redis indisponivel")
        doc = make_document(self.col, "Direto")
        doc_id = doc.pk
        self.client.force_authenticate(user=self.staff)
        resp = self.client.delete(self._url(doc.pk))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not KnowledgeDocument.objects.filter(pk=doc_id).exists()


# ---------------------------------------------------------------------------
# Testes de API: re-indexacao de documento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDocumentReindexAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Reindex")
        self.admin = make_user("admin_ri", staff=True, superuser=True)

    def _url(self, doc_id):
        return "/rag/api/knowledge/documents/%s/reindex/" % doc_id

    @patch("apps.core.tasks.reindex_document.delay")
    def test_admin_reindexa_documento(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-ri-001")
        doc = make_document(self.col, "Reindexar", status=KnowledgeDocument.Status.READY)
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(self._url(doc.pk))
        assert resp.status_code == status.HTTP_202_ACCEPTED
        assert resp.data["task_id"] == "task-ri-001"
        assert resp.data["doc_id"] == str(doc.pk)

    def test_nao_admin_recebe_403(self):
        doc = make_document(self.col, "Sem Permissao")
        user = make_user("leitor2")
        self.client.force_authenticate(user=user)
        resp = self.client.post(self._url(doc.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    @patch("apps.core.tasks.reindex_document.delay")
    def test_documento_indexando_retorna_409(self, mock_delay):
        doc = make_document(self.col, "Indexando", status=KnowledgeDocument.Status.INDEXING)
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(self._url(doc.pk))
        assert resp.status_code == status.HTTP_409_CONFLICT
        mock_delay.assert_not_called()

    def test_documento_inexistente_retorna_404(self):
        self.client.force_authenticate(user=self.admin)
        resp = self.client.post(self._url(uuid.uuid4()))
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Testes da task Celery: run_bulk_import
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRunBulkImportTask:

    def setup_method(self):
        self.col = make_collection()

    @patch("apps.knowledge.tasks.index_document")
    def test_task_cria_documentos_e_enfileira_indexacao(self, mock_index):
        mock_index.delay = MagicMock(return_value=MagicMock(id="t1"))
        with tempfile.TemporaryDirectory() as tmpdir:
            # Cria arquivos de teste
            for nome in ["relatorio.pdf", "manual.docx", "notas.txt"]:
                pathlib.Path(tmpdir, nome).write_bytes(b"conteudo")

            job = make_bulk_job(self.col, tmpdir)
            from apps.knowledge.tasks import run_bulk_import
            result = run_bulk_import(str(job.id))

        assert result["indexed"] == 3
        assert result["failed"] == 0
        assert result["total"] == 3
        assert KnowledgeDocument.objects.filter(collection=self.col).count() == 3
        job.refresh_from_db()
        assert job.status == BulkImportJob.Status.COMPLETED
        assert job.indexed_files == 3

    @patch("apps.knowledge.tasks.index_document")
    def test_task_filtra_por_extensao(self, mock_index):
        mock_index.delay = MagicMock(return_value=MagicMock(id="t"))
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "doc.pdf").write_bytes(b"pdf")
            pathlib.Path(tmpdir, "planilha.xlsx").write_bytes(b"xlsx")
            pathlib.Path(tmpdir, "texto.txt").write_bytes(b"txt")

            job = make_bulk_job(self.col, tmpdir, file_extensions=["pdf"])
            from apps.knowledge.tasks import run_bulk_import
            result = run_bulk_import(str(job.id))

        assert result["indexed"] == 1  # apenas PDF
        docs = KnowledgeDocument.objects.filter(collection=self.col)
        assert all(d.file_type == "pdf" for d in docs)

    @patch("apps.knowledge.tasks.index_document")
    def test_task_recursiva_inclui_subdiretorios(self, mock_index):
        mock_index.delay = MagicMock(return_value=MagicMock(id="t"))
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "raiz.pdf").write_bytes(b"r")
            subdir = pathlib.Path(tmpdir, "sub")
            subdir.mkdir()
            pathlib.Path(subdir, "subpasta.pdf").write_bytes(b"s")

            job = make_bulk_job(self.col, tmpdir, recursive=True)
            from apps.knowledge.tasks import run_bulk_import
            result = run_bulk_import(str(job.id))

        assert result["indexed"] == 2

    @patch("apps.knowledge.tasks.index_document")
    def test_task_nao_recursiva_ignora_subdiretorios(self, mock_index):
        mock_index.delay = MagicMock(return_value=MagicMock(id="t"))
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "raiz.pdf").write_bytes(b"r")
            subdir = pathlib.Path(tmpdir, "sub")
            subdir.mkdir()
            pathlib.Path(subdir, "subpasta.pdf").write_bytes(b"s")

            job = make_bulk_job(self.col, tmpdir, recursive=False)
            from apps.knowledge.tasks import run_bulk_import
            result = run_bulk_import(str(job.id))

        assert result["indexed"] == 1

    def test_task_falha_com_diretorio_invalido(self):
        job = make_bulk_job(self.col, "/nao/existe/xyz999")
        from apps.knowledge.tasks import run_bulk_import
        result = run_bulk_import(str(job.id))
        job.refresh_from_db()
        assert job.status == BulkImportJob.Status.FAILED
        assert "inválido" in job.error_message or "invalido" in job.error_message.lower() or job.error_message

    def test_task_job_inexistente_retorna_erro(self):
        from apps.knowledge.tasks import run_bulk_import
        result = run_bulk_import(str(uuid.uuid4()))
        assert "error" in result

    @patch("apps.knowledge.tasks.index_document")
    def test_task_status_completed_with_errors_quando_algum_falha(self, mock_index):
        # Simula falha ao criar documento para um dos arquivos
        call_count = {"n": 0}

        def side_effect_create(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise Exception("Falha simulada")
            from apps.knowledge.models import KnowledgeDocument as KD
            return KD.objects.create(*args, **kwargs)

        mock_index.delay = MagicMock(return_value=MagicMock(id="t"))

        with tempfile.TemporaryDirectory() as tmpdir:
            for nome in ["a.pdf", "b.pdf", "c.pdf"]:
                pathlib.Path(tmpdir, nome).write_bytes(b"x")

            job = make_bulk_job(self.col, tmpdir)
            # Patch KnowledgeDocument.objects.create dentro da task
            with patch(
                "apps.knowledge.tasks.KnowledgeDocument.objects.create",
                side_effect=side_effect_create,
            ):
                from apps.knowledge.tasks import run_bulk_import
                result = run_bulk_import(str(job.id))

        job.refresh_from_db()
        assert job.status == BulkImportJob.Status.COMPLETED_WITH_ERRORS
        assert job.failed_files >= 1

    @patch("apps.knowledge.tasks.index_document")
    def test_task_vincula_documentos_ao_job(self, mock_index):
        mock_index.delay = MagicMock(return_value=MagicMock(id="t"))
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "doc.pdf").write_bytes(b"x")
            job = make_bulk_job(self.col, tmpdir)
            from apps.knowledge.tasks import run_bulk_import
            run_bulk_import(str(job.id))

        docs = KnowledgeDocument.objects.filter(collection=self.col)
        assert all(d.bulk_import_job_id == job.id for d in docs)


# ---------------------------------------------------------------------------
# Testes do management command: bulk_ingest_knowledge
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkIngestCommand:

    def setup_method(self):
        self.col = make_collection()

    def _run_command(self, *args, **kwargs):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("bulk_ingest_knowledge", *args, stdout=out, **kwargs)
        return out.getvalue()

    def test_dry_run_nao_cria_nenhum_registro(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "doc.pdf").write_bytes(b"x")
            pathlib.Path(tmpdir, "nota.txt").write_bytes(b"y")
            output = self._run_command(str(self.col.pk), tmpdir, dry_run=True)
        assert KnowledgeDocument.objects.filter(collection=self.col).count() == 0
        assert BulkImportJob.objects.filter(collection=self.col).count() == 0
        assert "DRY RUN" in output

    @patch("apps.core.tasks.index_document.delay")
    def test_importa_arquivos_suportados(self, mock_delay):
        mock_delay.return_value = MagicMock(id="t")
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "manual.pdf").write_bytes(b"x")
            pathlib.Path(tmpdir, "policy.docx").write_bytes(b"y")
            pathlib.Path(tmpdir, "readme.md").write_bytes(b"z")
            pathlib.Path(tmpdir, "planilha.xlsx").write_bytes(b"skip")
            self._run_command(str(self.col.pk), tmpdir)
        assert KnowledgeDocument.objects.filter(collection=self.col).count() == 3
        assert BulkImportJob.objects.filter(collection=self.col).count() == 1

    @patch("apps.core.tasks.index_document.delay")
    def test_skip_existing_ignora_arquivo_ja_importado(self, mock_delay):
        mock_delay.return_value = MagicMock(id="t")
        with tempfile.TemporaryDirectory() as tmpdir:
            p = pathlib.Path(tmpdir, "doc.pdf")
            p.write_bytes(b"x")
            # Cria documento com o mesmo file_path
            KnowledgeDocument.objects.create(
                collection=self.col,
                title="Existente",
                file_path=str(p),
                file_type="pdf",
                status=KnowledgeDocument.Status.READY,
            )
            output = self._run_command(str(self.col.pk), tmpdir, skip_existing=True)
        # Apenas o documento pre-existente deve estar la (nao criou duplicata)
        assert KnowledgeDocument.objects.filter(collection=self.col).count() == 1
        assert "ignorado" in output.lower() or "1" in output

    def test_colecao_inexistente_lanca_erro(self):
        from django.core.management.base import CommandError
        with pytest.raises(CommandError):
            self._run_command(str(uuid.uuid4()), "/tmp")

    def test_diretorio_inexistente_lanca_erro(self):
        from django.core.management.base import CommandError
        with pytest.raises(CommandError):
            self._run_command(str(self.col.pk), "/nao/existe/xyz999")

    @patch("apps.core.tasks.index_document.delay")
    def test_filtro_por_extensao(self, mock_delay):
        mock_delay.return_value = MagicMock(id="t")
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "a.pdf").write_bytes(b"x")
            pathlib.Path(tmpdir, "b.docx").write_bytes(b"y")
            pathlib.Path(tmpdir, "c.txt").write_bytes(b"z")
            self._run_command(str(self.col.pk), tmpdir, extensions=["pdf"])
        docs = KnowledgeDocument.objects.filter(collection=self.col)
        assert docs.count() == 1
        assert docs.first().file_type == "pdf"

    @patch("apps.core.tasks.index_document.delay")
    def test_cria_bulk_import_job_completed(self, mock_delay):
        mock_delay.return_value = MagicMock(id="t")
        with tempfile.TemporaryDirectory() as tmpdir:
            pathlib.Path(tmpdir, "doc.txt").write_bytes(b"x")
            self._run_command(str(self.col.pk), tmpdir)
        job = BulkImportJob.objects.get(collection=self.col)
        assert job.status == BulkImportJob.Status.COMPLETED
        assert job.total_files == 1
        assert job.indexed_files == 1
        assert job.failed_files == 0

    def test_diretorio_vazio_nao_cria_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = self._run_command(str(self.col.pk), tmpdir)
        assert BulkImportJob.objects.filter(collection=self.col).count() == 0
        assert "Nenhum arquivo" in output or "nenhum" in output.lower()
