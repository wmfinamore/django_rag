"""
Testes da app documents.

Cobertura:
    TestUserDocumentModel           -- campos, __str__, is_ready, trigger_indexing/reindex
    TestUserChunkModel              -- campos, __str__, unique_together
    TestUserDocumentSerializer      -- campos, read_only, file_url
    TestUserDocumentUploadSerializer -- validacao de extensao e tamanho
    TestDocumentListAPI             -- GET /api/documents/ isolamento por usuario
    TestDocumentUploadAPI           -- POST /api/documents/ upload + Celery mock
    TestDocumentRetrieveAPI         -- GET /api/documents/<id>/ isolamento por usuario
    TestDocumentDestroyAPI          -- DELETE /api/documents/<id>/ + Celery mock
    TestDocumentReindexAPI          -- POST /api/documents/<id>/reindex/ + conflict

Convencoes:
    - Usa pytest-django (conforme pyproject.toml).
    - Celery e sempre mockado (@patch); testes nao dependem de Redis.
    - pgvector: chunks criados com embedding de zeros (lista de 0.0 x 384).
    - Todos os testes de API usam APIClient do DRF.
    - Uploads usam django.test.SimpleUploadedFile -- nao requerem disco real.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import CustomUser
from apps.documents.models import UserChunk, UserDocument
from apps.documents.serializers import UserDocumentSerializer, UserDocumentUploadSerializer


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

ZERO_EMBEDDING = [0.0] * 384


def make_user(username, *, staff=False, superuser=False) -> CustomUser:
    return CustomUser.objects.create_user(
        username=username,
        password="senha123",
        is_staff=staff,
        is_superuser=superuser,
    )


def make_document(
    owner,
    title="Doc Pessoal Teste",
    *,
    file_type="txt",
    status=UserDocument.Status.PENDING,
) -> UserDocument:
    doc = UserDocument(
        owner=owner,
        title=title,
        file_type=file_type,
        status=status,
    )
    # Salva sem arquivo real para testes de modelo
    doc.file.name = f"documents/2024/01/{uuid.uuid4().hex}.{file_type}"
    doc.save()
    return doc


def make_chunk(document, index=0) -> UserChunk:
    return UserChunk.objects.create(
        document=document,
        user_id=document.owner_id,
        chunk_index=index,
        content=f"Conteudo do chunk {index}",
        embedding=ZERO_EMBEDDING,
    )


# ---------------------------------------------------------------------------
# TestUserDocumentModel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserDocumentModel:
    """Testa o modelo UserDocument."""

    def test_campos_basicos(self):
        user = make_user("user_modelo")
        doc = make_document(user, "Meu Relatorio Anual", file_type="pdf")
        assert doc.id is not None
        assert doc.owner == user
        assert doc.title == "Meu Relatorio Anual"
        assert doc.file_type == "pdf"
        assert doc.status == "pending"
        assert doc.chunks_count == 0
        assert doc.error_message == ""
        assert doc.created_at is not None
        assert doc.updated_at is not None

    def test_str(self):
        user = make_user("user_str")
        doc = make_document(user, "Contrato 2024")
        assert str(doc) == "Contrato 2024 [Pendente]"

    def test_str_status_ready(self):
        user = make_user("user_str_ready")
        doc = make_document(user, "Ata de Reuniao", status=UserDocument.Status.READY)
        assert str(doc) == "Ata de Reuniao [Pronto]"

    def test_is_ready_false_quando_pending(self):
        user = make_user("user_is_ready_f")
        doc = make_document(user)
        assert doc.is_ready is False

    def test_is_ready_true_quando_ready(self):
        user = make_user("user_is_ready_t")
        doc = make_document(user, status=UserDocument.Status.READY)
        assert doc.is_ready is True

    def test_trigger_indexing(self):
        user = make_user("user_trigger_idx")
        doc = make_document(user)
        with patch("apps.core.tasks.index_document.delay") as mock_delay:
            mock_result = MagicMock()
            mock_result.id = "fake-task-id-123"
            mock_delay.return_value = mock_result
            task_id = doc.trigger_indexing()
        mock_delay.assert_called_once_with(str(doc.id), "personal")
        assert task_id == "fake-task-id-123"

    def test_trigger_reindex(self):
        user = make_user("user_trigger_reindex")
        doc = make_document(user)
        with patch("apps.core.tasks.reindex_document.delay") as mock_delay:
            mock_result = MagicMock()
            mock_result.id = "fake-reindex-id-456"
            mock_delay.return_value = mock_result
            task_id = doc.trigger_reindex()
        mock_delay.assert_called_once_with(str(doc.id), "personal")
        assert task_id == "fake-reindex-id-456"

    def test_status_choices(self):
        choices = [c[0] for c in UserDocument.Status.choices]
        assert "pending" in choices
        assert "indexing" in choices
        assert "ready" in choices
        assert "error" in choices

    def test_file_type_choices(self):
        choices = [c[0] for c in UserDocument.FileType.choices]
        assert "pdf" in choices
        assert "docx" in choices
        assert "txt" in choices
        assert "md" in choices

    def test_cascade_delete_com_owner(self):
        user = make_user("user_cascade")
        doc = make_document(user)
        doc_id = doc.id
        user.delete()
        assert not UserDocument.objects.filter(pk=doc_id).exists()

    def test_ordering_por_created_at_desc(self):
        user = make_user("user_order")
        doc1 = make_document(user, "Doc 1")
        doc2 = make_document(user, "Doc 2")
        docs = list(UserDocument.objects.filter(owner=user))
        assert docs[0] == doc2  # mais recente primeiro
        assert docs[1] == doc1


# ---------------------------------------------------------------------------
# TestUserChunkModel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserChunkModel:
    """Testa o modelo UserChunk."""

    def test_campos_basicos(self):
        user = make_user("user_chunk")
        doc = make_document(user)
        chunk = make_chunk(doc, index=0)
        assert chunk.id is not None
        assert chunk.document == doc
        assert chunk.user_id == doc.owner_id
        assert chunk.chunk_index == 0
        assert chunk.content == "Conteudo do chunk 0"
        assert len(chunk.embedding) == 384

    def test_str(self):
        user = make_user("user_chunk_str")
        doc = make_document(user, "Proposta Comercial")
        chunk = make_chunk(doc, index=2)
        assert str(chunk) == "Chunk 2 -- Proposta Comercial"

    def test_unique_together(self):
        """Dois chunks com mesmo documento e chunk_index devem falhar."""
        user = make_user("user_chunk_unique")
        doc = make_document(user)
        make_chunk(doc, index=0)
        with pytest.raises(Exception):
            make_chunk(doc, index=0)

    def test_cascade_delete_com_document(self):
        user = make_user("user_chunk_cascade")
        doc = make_document(user)
        chunk = make_chunk(doc, index=0)
        chunk_id = chunk.id
        doc.delete()
        assert not UserChunk.objects.filter(pk=chunk_id).exists()

    def test_user_id_desnormalizado(self):
        """user_id deve corresponder ao owner.id do documento."""
        user = make_user("user_chunk_desnorm")
        doc = make_document(user)
        chunk = make_chunk(doc, index=0)
        assert chunk.user_id == user.id


# ---------------------------------------------------------------------------
# TestUserDocumentSerializer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserDocumentSerializer:
    """Testa o UserDocumentSerializer."""

    def test_campos_presentes(self):
        user = make_user("user_ser")
        doc = make_document(user, "Doc Serializado")
        serializer = UserDocumentSerializer(doc)
        data = serializer.data
        assert "id" in data
        assert "owner" in data
        assert "owner_username" in data
        assert "title" in data
        assert "file" in data
        assert "file_url" in data
        assert "file_type" in data
        assert "status" in data
        assert "status_display" in data
        assert "chunks_count" in data
        assert "error_message" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_owner_username(self):
        user = make_user("user_ser_username")
        doc = make_document(user)
        serializer = UserDocumentSerializer(doc)
        assert serializer.data["owner_username"] == "user_ser_username"

    def test_status_display(self):
        user = make_user("user_ser_status")
        doc = make_document(user, status=UserDocument.Status.READY)
        serializer = UserDocumentSerializer(doc)
        assert serializer.data["status_display"] == "Pronto"

    def test_file_url_sem_request(self):
        user = make_user("user_ser_url")
        doc = make_document(user)
        serializer = UserDocumentSerializer(doc)
        assert serializer.data["file_url"] is None

    def test_campos_read_only(self):
        """Campos somente-leitura devem ser ignorados na escrita."""
        user = make_user("user_ser_ro")
        doc = make_document(user)
        serializer = UserDocumentSerializer(doc)
        assert "owner" in serializer.Meta.read_only_fields
        assert "status" in serializer.Meta.read_only_fields
        assert "chunks_count" in serializer.Meta.read_only_fields


# ---------------------------------------------------------------------------
# TestUserDocumentUploadSerializer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserDocumentUploadSerializer:
    """Testa o UserDocumentUploadSerializer."""

    def _make_file(self, name="doc.txt", size=100):
        content = b"x" * size
        return SimpleUploadedFile(name, content)

    def test_valido_txt(self):
        data = {"title": "Documento TXT", "file": self._make_file("relatorio.txt")}
        serializer = UserDocumentUploadSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_valido_pdf(self):
        data = {"title": "Documento PDF", "file": self._make_file("manual.pdf")}
        serializer = UserDocumentUploadSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_valido_docx(self):
        data = {"title": "Documento DOCX", "file": self._make_file("contrato.docx")}
        serializer = UserDocumentUploadSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_valido_md(self):
        data = {"title": "Documento MD", "file": self._make_file("notas.md")}
        serializer = UserDocumentUploadSerializer(data=data)
        assert serializer.is_valid(), serializer.errors

    def test_extensao_invalida_rejeita(self):
        data = {"title": "Exe malicioso", "file": self._make_file("virus.exe")}
        serializer = UserDocumentUploadSerializer(data=data)
        assert not serializer.is_valid()
        assert "file" in serializer.errors

    def test_extensao_csv_invalida(self):
        data = {"title": "Planilha", "file": self._make_file("dados.csv")}
        serializer = UserDocumentUploadSerializer(data=data)
        assert not serializer.is_valid()
        assert "file" in serializer.errors

    def test_arquivo_muito_grande_rejeita(self):
        tamanho_51mb = 51 * 1024 * 1024
        f = SimpleUploadedFile("grande.pdf", b"x" * tamanho_51mb)
        data = {"title": "Arquivo Gigante", "file": f}
        serializer = UserDocumentUploadSerializer(data=data)
        assert not serializer.is_valid()
        assert "file" in serializer.errors

    def test_titulo_obrigatorio(self):
        data = {"file": self._make_file("doc.txt")}
        serializer = UserDocumentUploadSerializer(data=data)
        assert not serializer.is_valid()
        assert "title" in serializer.errors

    def test_arquivo_obrigatorio(self):
        data = {"title": "Sem arquivo"}
        serializer = UserDocumentUploadSerializer(data=data)
        assert not serializer.is_valid()
        assert "file" in serializer.errors


# ---------------------------------------------------------------------------
# TestDocumentListAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentListAPI:
    """Testa GET /api/documents/."""

    def setup_method(self):
        self.client = APIClient()
        self.url = "/rag/api/documents/"

    def test_anonimo_recebe_403(self):
        # DRF com sessao retorna 403 (nao 401) para requisicoes sem autenticacao
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_usuario_ve_apenas_seus_docs(self):
        user1 = make_user("user_list_1")
        user2 = make_user("user_list_2")
        make_document(user1, "Doc de User1")
        make_document(user2, "Doc de User2")

        self.client.force_authenticate(user=user1)
        response = self.client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        results = response.data["results"]
        ids = [d["id"] for d in results]
        assert all(
            UserDocument.objects.get(pk=i).owner == user1 for i in ids
        )

    def test_lista_vazia_retorna_200(self):
        user = make_user("user_list_empty")
        self.client.force_authenticate(user=user)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["results"] == []

    def test_multiplos_docs_listados(self):
        user = make_user("user_list_multi")
        make_document(user, "Doc A")
        make_document(user, "Doc B")
        make_document(user, "Doc C")
        self.client.force_authenticate(user=user)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 3

    def test_superuser_ve_apenas_seus_docs(self):
        """Superuser nao tem acesso especial a docs de outros usuarios."""
        admin = make_user("admin_list", superuser=True)
        outro = make_user("outro_list")
        make_document(outro, "Doc de Outro")

        self.client.force_authenticate(user=admin)
        response = self.client.get(self.url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["count"] == 0  # admin nao tem docs proprios


# ---------------------------------------------------------------------------
# TestDocumentUploadAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentUploadAPI:
    """Testa POST /api/documents/."""

    def setup_method(self):
        self.client = APIClient()
        self.url = "/rag/api/documents/"

    def _make_file(self, name="doc.txt", content=b"conteudo de teste"):
        return SimpleUploadedFile(name, content)

    def test_anonimo_recebe_403(self):
        response = self.client.post(self.url, {})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch("apps.core.tasks.index_document.delay")
    def test_upload_valido_cria_documento(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-upload-ok")
        user = make_user("user_upload_ok")
        self.client.force_authenticate(user=user)

        data = {
            "title": "Meu Relatorio",
            "file": self._make_file("relatorio.txt"),
        }
        response = self.client.post(self.url, data, format="multipart")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["title"] == "Meu Relatorio"
        assert response.data["file_type"] == "txt"
        assert response.data["status"] == "pending"
        assert response.data["task_id"] == "task-upload-ok"
        assert UserDocument.objects.filter(owner=user, title="Meu Relatorio").exists()

    @patch("apps.core.tasks.index_document.delay")
    def test_upload_pdf_valido(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-pdf-ok")
        user = make_user("user_upload_pdf")
        self.client.force_authenticate(user=user)

        data = {
            "title": "Manual Tecnico",
            "file": self._make_file("manual.pdf", b"%PDF-1.4 conteudo"),
        }
        response = self.client.post(self.url, data, format="multipart")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["file_type"] == "pdf"

    def test_upload_extensao_invalida_retorna_400(self):
        user = make_user("user_upload_bad_ext")
        self.client.force_authenticate(user=user)

        data = {
            "title": "Script Perigoso",
            "file": self._make_file("script.sh", b"#!/bin/bash"),
        }
        response = self.client.post(self.url, data, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file" in response.data

    def test_upload_sem_titulo_retorna_400(self):
        user = make_user("user_upload_no_title")
        self.client.force_authenticate(user=user)

        data = {"file": self._make_file("doc.txt")}
        response = self.client.post(self.url, data, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "title" in response.data

    def test_upload_sem_arquivo_retorna_400(self):
        user = make_user("user_upload_no_file")
        self.client.force_authenticate(user=user)

        data = {"title": "Sem arquivo"}
        response = self.client.post(self.url, data, format="multipart")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "file" in response.data

    @patch("apps.core.tasks.index_document.delay", side_effect=Exception("Redis offline"))
    def test_upload_sem_celery_ainda_cria_documento(self, mock_delay):
        """Se Celery falhar, o documento deve ser criado mas sem task_id."""
        user = make_user("user_upload_no_celery")
        self.client.force_authenticate(user=user)

        data = {
            "title": "Doc sem Celery",
            "file": self._make_file("doc.txt"),
        }
        response = self.client.post(self.url, data, format="multipart")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["task_id"] is None
        assert UserDocument.objects.filter(owner=user).exists()

    @patch("apps.core.tasks.index_document.delay")
    def test_upload_define_owner_como_usuario_autenticado(self, mock_delay):
        """O owner do documento deve ser o usuario que fez o upload."""
        mock_delay.return_value = MagicMock(id="task-owner-ok")
        user = make_user("user_upload_owner")
        outro = make_user("outro_upload_owner")
        self.client.force_authenticate(user=user)

        data = {
            "title": "Doc do User",
            "file": self._make_file("doc.txt"),
        }
        self.client.post(self.url, data, format="multipart")

        doc = UserDocument.objects.get(owner=user, title="Doc do User")
        assert doc.owner == user


# ---------------------------------------------------------------------------
# TestDocumentRetrieveAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentRetrieveAPI:
    """Testa GET /api/documents/<id>/."""

    def setup_method(self):
        self.client = APIClient()

    def _url(self, doc_id):
        return f"/rag/api/documents/{doc_id}/"

    def test_anonimo_recebe_403(self):
        user = make_user("user_ret_anon")
        doc = make_document(user)
        response = self.client.get(self._url(doc.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_proprietario_acessa_seu_doc(self):
        user = make_user("user_ret_owner")
        doc = make_document(user, "Meu Documento")
        self.client.force_authenticate(user=user)
        response = self.client.get(self._url(doc.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data["title"] == "Meu Documento"

    def test_outro_usuario_recebe_404(self):
        owner = make_user("user_ret_owner2")
        intruso = make_user("user_ret_intruso")
        doc = make_document(owner)
        self.client.force_authenticate(user=intruso)
        response = self.client.get(self._url(doc.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_doc_inexistente_retorna_404(self):
        user = make_user("user_ret_404")
        self.client.force_authenticate(user=user)
        response = self.client.get(self._url(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_campos_na_resposta(self):
        user = make_user("user_ret_campos")
        doc = make_document(user, "Doc Completo", file_type="pdf",
                            status=UserDocument.Status.READY)
        self.client.force_authenticate(user=user)
        response = self.client.get(self._url(doc.id))
        data = response.data
        assert data["id"] == str(doc.id)
        assert data["title"] == "Doc Completo"
        assert data["file_type"] == "pdf"
        assert data["status"] == "ready"
        assert data["status_display"] == "Pronto"
        assert data["owner_username"] == "user_ret_campos"


# ---------------------------------------------------------------------------
# TestDocumentDestroyAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentDestroyAPI:
    """Testa DELETE /api/documents/<id>/."""

    def setup_method(self):
        self.client = APIClient()

    def _url(self, doc_id):
        return f"/rag/api/documents/{doc_id}/"

    def test_anonimo_recebe_403(self):
        user = make_user("user_del_anon")
        doc = make_document(user)
        response = self.client.delete(self._url(doc.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch("apps.core.tasks.delete_document.delay")
    def test_proprietario_pode_deletar(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-del-ok")
        user = make_user("user_del_ok")
        doc = make_document(user)
        self.client.force_authenticate(user=user)
        response = self.client.delete(self._url(doc.id))
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["doc_id"] == str(doc.id)
        mock_delay.assert_called_once_with(str(doc.id), "personal")

    def test_outro_usuario_recebe_404(self):
        owner = make_user("user_del_owner")
        intruso = make_user("user_del_intruso")
        doc = make_document(owner)
        self.client.force_authenticate(user=intruso)
        response = self.client.delete(self._url(doc.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @patch("apps.core.tasks.delete_document.delay", side_effect=Exception("Redis offline"))
    def test_fallback_sincrono_quando_celery_falha(self, mock_delay):
        """Se Celery falhar, deleta sincronamente e retorna 204."""
        user = make_user("user_del_no_celery")
        doc = make_document(user)
        chunk = make_chunk(doc, index=0)
        self.client.force_authenticate(user=user)
        response = self.client.delete(self._url(doc.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not UserDocument.objects.filter(pk=doc.id).exists()
        assert not UserChunk.objects.filter(pk=chunk.id).exists()

    def test_doc_inexistente_retorna_404(self):
        user = make_user("user_del_404")
        self.client.force_authenticate(user=user)
        response = self.client.delete(self._url(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# TestDocumentReindexAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDocumentReindexAPI:
    """Testa POST /api/documents/<id>/reindex/."""

    def setup_method(self):
        self.client = APIClient()

    def _url(self, doc_id):
        return f"/rag/api/documents/{doc_id}/reindex/"

    def test_anonimo_recebe_403(self):
        user = make_user("user_reindex_anon")
        doc = make_document(user)
        response = self.client.post(self._url(doc.id))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @patch("apps.core.tasks.reindex_document.delay")
    def test_proprietario_pode_reindexar(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-reindex-ok")
        user = make_user("user_reindex_ok")
        doc = make_document(user, status=UserDocument.Status.READY)
        self.client.force_authenticate(user=user)
        response = self.client.post(self._url(doc.id))
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["doc_id"] == str(doc.id)
        assert response.data["task_id"] == "task-reindex-ok"

    def test_outro_usuario_recebe_404(self):
        owner = make_user("user_reindex_owner")
        intruso = make_user("user_reindex_intruso")
        doc = make_document(owner, status=UserDocument.Status.READY)
        self.client.force_authenticate(user=intruso)
        response = self.client.post(self._url(doc.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_documento_em_indexacao_retorna_409(self):
        """Se o documento ja esta sendo indexado, retorna 409 Conflict."""
        user = make_user("user_reindex_conflict")
        doc = make_document(user, status=UserDocument.Status.INDEXING)
        self.client.force_authenticate(user=user)
        response = self.client.post(self._url(doc.id))
        assert response.status_code == status.HTTP_409_CONFLICT
        assert "ja esta sendo indexado" in response.data["detail"]

    def test_doc_inexistente_retorna_404(self):
        user = make_user("user_reindex_404")
        self.client.force_authenticate(user=user)
        response = self.client.post(self._url(uuid.uuid4()))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @patch("apps.core.tasks.reindex_document.delay", side_effect=Exception("Redis offline"))
    def test_falha_no_celery_retorna_500(self, mock_delay):
        user = make_user("user_reindex_500")
        doc = make_document(user, status=UserDocument.Status.READY)
        self.client.force_authenticate(user=user)
        response = self.client.post(self._url(doc.id))
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
