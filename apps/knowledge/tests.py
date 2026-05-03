"""
Testes da app knowledge.

Cobertura:
    TestKnowledgeCollectionModel     — campos, __str__, is_accessible_by
    TestKnowledgeDocumentModel       — campos, __str__, is_ready, trigger_indexing/reindex
    TestKnowledgeChunkModel          — campos, __str__, unique_together
    TestKnowledgeCollectionSerializer— campos computados, read_only
    TestKnowledgeDocumentUploadSerializer — validação de extensão, tamanho, coleção inativa
    TestIsStaffOrReadOnly            — permissão customizada
    TestCollectionListAPI            — GET /collections/ filtragem por grupo/superuser
    TestCollectionRetrieveAPI        — GET /collections/<id>/ controle de acesso
    TestCollectionCreateAPI          — POST /collections/ staff vs. não-staff
    TestCollectionDocumentsListAPI   — GET /collections/<id>/documents/ filtro por status
    TestCollectionDocumentsUploadAPI — POST /collections/<id>/documents/ upload + Celery mock
    TestDocumentRetrieveAPI          — GET /documents/<id>/ controle de acesso
    TestDocumentDestroyAPI           — DELETE /documents/<id>/ staff + Celery mock
    TestDocumentReindexAPI           — POST /documents/<id>/reindex/ staff + conflict

Convenções:
    - Usa ``pytest-django`` (conforme pyproject.toml).
    - Celery é sempre mockado (``@patch``); testes não dependem de Redis.
    - pgvector: chunks criados com embedding de zeros (lista de 0.0 × 384).
    - Todos os testes de API usam ``APIClient`` do DRF.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import CustomUser
from apps.knowledge.models import KnowledgeChunk, KnowledgeCollection, KnowledgeDocument
from apps.knowledge.serializers import (
    KnowledgeCollectionSerializer,
    KnowledgeDocumentUploadSerializer,
)
from apps.knowledge.views import IsStaffOrReadOnly


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

ZERO_EMBEDDING = [0.0] * 384


def make_user(username, *, staff=False, superuser=False, groups=()) -> CustomUser:
    """Cria e retorna um CustomUser para uso nos testes."""
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


def make_collection(name="Coleção Teste", *, active=True, groups=()) -> KnowledgeCollection:
    """Cria e retorna uma KnowledgeCollection."""
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
) -> KnowledgeDocument:
    """Cria e retorna um KnowledgeDocument."""
    return KnowledgeDocument.objects.create(
        collection=collection,
        title=title,
        file_path=f"/tmp/{uuid.uuid4().hex}.{file_type}",
        file_type=file_type,
        status=status,
        ingested_by=ingested_by,
    )


def make_chunk(document, index=0) -> KnowledgeChunk:
    """Cria e retorna um KnowledgeChunk com embedding zero."""
    return KnowledgeChunk.objects.create(
        document=document,
        collection_id=document.collection_id,
        chunk_index=index,
        content=f"Conteúdo do chunk {index}.",
        embedding=ZERO_EMBEDDING,
    )


# ---------------------------------------------------------------------------
# Testes de modelo: KnowledgeCollection
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestKnowledgeCollectionModel:

    def test_str(self):
        col = make_collection("RH – Férias")
        assert str(col) == "RH – Férias"

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
        make_collection("Nome Único")
        from django.db import IntegrityError
        with pytest.raises(IntegrityError):
            KnowledgeCollection.objects.create(name="Nome Único")

    # --- is_accessible_by ---

    def test_inacessivel_se_inativa(self):
        col = make_collection("Inativa", active=False)
        user = make_user("u1")
        assert col.is_accessible_by(user) is False

    def test_superuser_sempre_acessa(self):
        col = make_collection("Restrita", groups=["rh"])
        super_user = make_user("super", superuser=True)
        assert col.is_accessible_by(super_user) is True

    def test_colecao_publica_acessivel_por_qualquer_usuario(self):
        col = make_collection("Pública")  # sem grupos restritos
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

    def test_multiplos_chunks_mesmos_documento(self):
        for i in range(5):
            make_chunk(self.doc, index=i)
        assert self.doc.chunks.count() == 5


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
        col = make_collection("Jurídico", groups=["juridico"])
        data = KnowledgeCollectionSerializer(col).data
        assert "juridico" in data["allowed_groups"]

    def test_id_e_timestamps_sao_read_only(self):
        """Campos read_only não devem ser alteráveis via deserialização."""
        col = make_collection("Existente")
        original_id = str(col.pk)
        # Tenta passar id diferente — deve ser ignorado
        s = KnowledgeCollectionSerializer(col, data={"id": str(uuid.uuid4()), "name": "Existente"})
        s.is_valid()
        assert str(col.pk) == original_id


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
            data={
                "title": "Teste",
                "file": self._make_file("doc.txt"),
                "collection_id": str(self.col.pk),
            },
            context={"request": self._make_request()},
        )
        assert s.is_valid(), s.errors

    def test_valida_extensao_pdf(self):
        s = KnowledgeDocumentUploadSerializer(
            data={
                "title": "PDF Teste",
                "file": self._make_file("doc.pdf"),
                "collection_id": str(self.col.pk),
            },
            context={"request": self._make_request()},
        )
        assert s.is_valid(), s.errors

    def test_rejeita_extensao_invalida(self):
        s = KnowledgeDocumentUploadSerializer(
            data={
                "title": "Inválido",
                "file": self._make_file("virus.exe"),
                "collection_id": str(self.col.pk),
            },
            context={"request": self._make_request()},
        )
        assert not s.is_valid()
        assert "file" in s.errors

    def test_rejeita_arquivo_muito_grande(self):
        tamanho_51mb = 51 * 1024 * 1024
        s = KnowledgeDocumentUploadSerializer(
            data={
                "title": "Grande",
                "file": self._make_file("grande.pdf", size=tamanho_51mb),
                "collection_id": str(self.col.pk),
            },
            context={"request": self._make_request()},
        )
        assert not s.is_valid()
        assert "file" in s.errors

    def test_rejeita_colecao_inexistente(self):
        s = KnowledgeDocumentUploadSerializer(
            data={
                "title": "Sem Coleção",
                "file": self._make_file("doc.txt"),
                "collection_id": str(uuid.uuid4()),
            },
            context={"request": self._make_request()},
        )
        assert not s.is_valid()
        assert "collection_id" in s.errors

    def test_rejeita_colecao_inativa(self):
        col_inativa = make_collection("Inativa", active=False)
        s = KnowledgeDocumentUploadSerializer(
            data={
                "title": "Doc",
                "file": self._make_file("doc.txt"),
                "collection_id": str(col_inativa.pk),
            },
            context={"request": self._make_request()},
        )
        assert not s.is_valid()
        assert "collection_id" in s.errors

    def test_rejeita_usuario_sem_acesso_a_colecao_restrita(self):
        col_restrita = make_collection("Restrita", groups=["rh"])
        user_sem_grupo = make_user("forasteiro")
        mock_req = self._make_request(user=user_sem_grupo)
        s = KnowledgeDocumentUploadSerializer(
            data={
                "title": "Proibido",
                "file": self._make_file("doc.txt"),
                "collection_id": str(col_restrita.pk),
            },
            context={"request": mock_req},
        )
        assert not s.is_valid()


# ---------------------------------------------------------------------------
# Testes da permissão IsStaffOrReadOnly
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIsStaffOrReadOnly:

    def _make_request(self, method, *, authenticated=True, staff=False):
        req = MagicMock()
        req.method = method
        if authenticated:
            # Usa usuário real do Django — is_authenticated é property somente-leitura
            req.user = make_user(f"user_{uuid.uuid4().hex[:6]}", staff=staff)
        else:
            # Simula AnonymousUser: is_authenticated retorna False
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
# Testes de API: listagem de coleções
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
        make_collection("Pública A")
        make_collection("Pública B")
        user = make_user("viewer")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self.BASE_URL)
        assert resp.status_code == status.HTTP_200_OK
        nomes = [c["name"] for c in resp.data["results"]]
        assert "Pública A" in nomes
        assert "Pública B" in nomes

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
        make_collection("Pública")
        make_collection("Restrita", groups=["rh"])
        super_user = make_user("super", superuser=True)
        self.client.force_authenticate(user=super_user)
        resp = self.client.get(self.BASE_URL)
        assert resp.status_code == status.HTTP_200_OK
        nomes = [c["name"] for c in resp.data["results"]]
        assert "Pública" in nomes
        assert "Restrita" in nomes

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
# Testes de API: detalhe de coleção
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCollectionRetrieveAPI:

    def setup_method(self):
        self.client = APIClient()

    def _url(self, col_id):
        return f"/rag/api/knowledge/collections/{col_id}/"

    def test_usuario_acessa_colecao_publica(self):
        col = make_collection("Pública")
        user = make_user("viewer")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(col.pk))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["name"] == "Pública"

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
            assert campo in resp.data, f"Campo '{campo}' ausente na resposta"


# ---------------------------------------------------------------------------
# Testes de API: criação de coleção
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCollectionCreateAPI:

    BASE_URL = "/rag/api/knowledge/collections/"

    def setup_method(self):
        self.client = APIClient()

    def test_nao_staff_recebe_403(self):
        user = make_user("leitor")
        self.client.force_authenticate(user=user)
        resp = self.client.post(self.BASE_URL, {"name": "Nova Coleção"}, format="json")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_staff_cria_colecao(self):
        staff = make_user("staff", staff=True)
        self.client.force_authenticate(user=staff)
        resp = self.client.post(
            self.BASE_URL,
            {"name": "Nova Coleção", "description": "Teste"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert KnowledgeCollection.objects.filter(name="Nova Coleção").exists()

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
# Testes de API: listagem de documentos de uma coleção
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCollectionDocumentsListAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Docs")

    def _url(self, col_id):
        return f"/rag/api/knowledge/collections/{col_id}/documents/"

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
        titulos = [d["title"] for d in resp.data]
        assert "Pronto" in titulos
        assert "Pendente" not in titulos

    def test_usuario_sem_acesso_recebe_403(self):
        col_restrita = make_collection("Restrita Docs", groups=["rh"])
        user = make_user("forasteiro")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(col_restrita.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_colecao_inexistente_retorna_404(self):
        user = make_user("viewer3")
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
        return f"/rag/api/knowledge/collections/{col_id}/documents/"

    def _make_upload_file(self, name="documento.txt", content=b"conteudo do arquivo"):
        return io.BytesIO(content), name

    @patch("apps.core.tasks.index_document.delay")
    def test_staff_faz_upload_com_sucesso(self, mock_delay):
        mock_result = MagicMock()
        mock_result.id = "task-upload-001"
        mock_delay.return_value = mock_result

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
    def test_upload_cria_documento_com_file_type_correto(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-xyz")
        self.client.force_authenticate(user=self.staff)

        arquivo = SimpleUploadedFile("relatorio.pdf", b"conteudo pdf simulado", content_type="application/pdf")
        resp = self.client.post(
            self._url(self.col.pk),
            {"title": "Relatório PDF", "file": arquivo},
            format="multipart",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        doc = KnowledgeDocument.objects.get(title="Relatório PDF")
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
            self._url(self.col.pk),
            {"file": arquivo},
            format="multipart",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_upload_extensao_invalida_retorna_400(self):
        self.client.force_authenticate(user=self.staff)
        arquivo = SimpleUploadedFile("dados.xlsx", b"dados", content_type="application/vnd.ms-excel")
        resp = self.client.post(
            self._url(self.col.pk),
            {"title": "Excel Inválido", "file": arquivo},
            format="multipart",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Testes de API: detalhe do documento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDocumentRetrieveAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Doc Detail")

    def _url(self, doc_id):
        return f"/rag/api/knowledge/documents/{doc_id}/"

    def test_usuario_ve_documento_de_colecao_publica(self):
        doc = make_document(self.col, "Doc Público")
        user = make_user("viewer")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(doc.pk))
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["title"] == "Doc Público"

    def test_resposta_contem_campos_esperados(self):
        doc = make_document(self.col, "Completo")
        user = make_user("viewer2")
        self.client.force_authenticate(user=user)
        resp = self.client.get(self._url(doc.pk))
        for campo in ["id", "title", "collection", "file_type", "status", "chunks_count"]:
            assert campo in resp.data, f"Campo '{campo}' ausente"

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
# Testes de API: deleção de documento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDocumentDestroyAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Delete")
        self.staff = make_user("staff_del", staff=True)

    def _url(self, doc_id):
        return f"/rag/api/knowledge/documents/{doc_id}/"

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
        # staff sem o grupo rh
        staff_sem_grupo = make_user("staff_sg", staff=True)
        self.client.force_authenticate(user=staff_sem_grupo)
        resp = self.client.delete(self._url(doc.pk))
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        mock_delay.assert_not_called()

    @patch("apps.core.tasks.delete_document.delay")
    def test_fallback_deleta_direto_se_celery_falhar(self, mock_delay):
        """Se o Celery não estiver disponível, o documento deve ser deletado diretamente."""
        mock_delay.side_effect = Exception("Redis indisponível")
        doc = make_document(self.col, "Direto")
        doc_id = doc.pk
        self.client.force_authenticate(user=self.staff)
        resp = self.client.delete(self._url(doc.pk))
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        assert not KnowledgeDocument.objects.filter(pk=doc_id).exists()


# ---------------------------------------------------------------------------
# Testes de API: re-indexação de documento
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDocumentReindexAPI:

    def setup_method(self):
        self.client = APIClient()
        self.col = make_collection("Col Reindex")
        self.admin = make_user("admin_ri", staff=True, superuser=True)

    def _url(self, doc_id):
        return f"/rag/api/knowledge/documents/{doc_id}/reindex/"

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
        doc = make_document(self.col, "Sem Permissão")
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
