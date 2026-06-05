"""
Testes da app chat.

Cobertura:
    TestConversationModel           -- campos, __str__, relationships
    TestMessageModel                -- campos, __str__, role choices
    TestConversationSerializer      -- leitura: message_count, last_message, collection_ids
    TestConversationCreateSerializer -- escrita: create, update, validacao de colecoes
    TestConversationListAPI         -- GET /api/chat/conversations/ isolamento por usuario
    TestConversationCreateAPI       -- POST /api/chat/conversations/ criacao e validacao
    TestConversationRetrieveAPI     -- GET /api/chat/conversations/<id>/ isolamento
    TestConversationUpdateAPI       -- PATCH /api/chat/conversations/<id>/ atualizacao parcial
    TestConversationDestroyAPI      -- DELETE /api/chat/conversations/<id>/ isolamento
    TestConversationMessagesAPI     -- GET /api/chat/conversations/<id>/messages/ historico
    TestChatIndexView               -- GET /chat/ autenticacao e renderizacao
    TestHxConversationList          -- GET /chat/hx/conversations/ fragment HTMX
    TestHxConversationDetail        -- GET /chat/hx/conversations/<id>/ fragment HTMX
    TestHxConversationCreate        -- POST /chat/hx/conversations/new/ criacao
    TestHxConversationDelete        -- POST /chat/hx/conversations/<id>/delete/ exclusao
    TestHxConversationRename        -- POST /chat/hx/conversations/<id>/rename/ renomear
    TestChatConsumer                -- WebSocket: auth, connect, receive, streaming

Convencoes:
    - pytest-django (pyproject.toml DJANGO_SETTINGS_MODULE=config.settings.development).
    - RAGService e LLM sempre mockados; testes nao dependem de Ollama/modelos ML.
    - WebSocket: scope["user"] injetado via middleware de teste (sem Redis/sessao real).
    - APIClient do DRF para REST; Client do Django para views HTML.
    - Celery nao e usado nesta app; sem mocks de tasks.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.test import AsyncClient, Client
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import CustomUser
from apps.chat.models import Conversation, Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_user(username: str, *, staff=False, superuser=False) -> CustomUser:
    return CustomUser.objects.create_user(
        username=username, password="senha123", is_staff=staff, is_superuser=superuser
    )


def make_conversation(
    user: CustomUser,
    title: str = "Conversa Teste",
    *,
    use_personal_docs: bool = False,
) -> Conversation:
    return Conversation.objects.create(
        user=user, title=title, use_personal_docs=use_personal_docs
    )


def make_message(
    conversation: Conversation,
    role: str = "user",
    content: str = "Mensagem de teste",
    sources: list | None = None,
) -> Message:
    return Message.objects.create(
        conversation=conversation,
        role=role,
        content=content,
        sources=sources or [],
    )


# ---------------------------------------------------------------------------
# TestConversationModel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversationModel:
    def test_campos_basicos(self):
        user = make_user("u_conv_campos")
        conv = make_conversation(user, "Minha Conversa")
        assert conv.id is not None
        assert conv.user == user
        assert conv.title == "Minha Conversa"
        assert conv.use_personal_docs is False
        assert conv.created_at is not None
        assert conv.updated_at is not None

    def test_str(self):
        user = make_user("u_conv_str")
        conv = make_conversation(user, "Politica de Ferias")
        assert str(conv) == "Politica de Ferias (u_conv_str)"

    def test_default_title(self):
        user = make_user("u_conv_def")
        conv = Conversation.objects.create(user=user)
        assert conv.title == "Nova conversa"

    def test_ordering_por_updated_at(self):
        user = make_user("u_conv_order")
        c1 = make_conversation(user, "Antiga")
        c2 = make_conversation(user, "Recente")
        c2.save()  # atualiza updated_at de c2
        convs = list(Conversation.objects.filter(user=user))
        assert convs[0] == c2

    def test_cascade_deleta_mensagens(self):
        user = make_user("u_conv_cascade")
        conv = make_conversation(user)
        make_message(conv, "user", "Ola")
        make_message(conv, "assistant", "Resposta")
        assert Message.objects.filter(conversation=conv).count() == 2
        conv.delete()
        assert Message.objects.count() == 0


# ---------------------------------------------------------------------------
# TestMessageModel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMessageModel:
    def test_campos_basicos(self):
        user = make_user("u_msg_campos")
        conv = make_conversation(user)
        msg = make_message(conv, "user", "Qual e a politica?")
        assert msg.id is not None
        assert msg.conversation == conv
        assert msg.role == "user"
        assert msg.content == "Qual e a politica?"
        assert msg.sources == []
        assert msg.created_at is not None

    def test_str_user(self):
        user = make_user("u_msg_str")
        conv = make_conversation(user)
        msg = make_message(conv, "user", "Pergunta longa " * 5)
        assert str(msg).startswith("[user]")

    def test_str_assistant(self):
        user = make_user("u_msg_str2")
        conv = make_conversation(user)
        msg = make_message(conv, "assistant", "Resposta do assistente")
        assert "[assistant]" in str(msg)

    def test_sources_json(self):
        user = make_user("u_msg_sources")
        conv = make_conversation(user)
        sources = [{"title": "Doc A", "id": str(uuid.uuid4()), "type": "knowledge", "index": 1}]
        msg = make_message(conv, "assistant", "Resposta com fonte", sources)
        msg.refresh_from_db()
        assert msg.sources[0]["title"] == "Doc A"

    def test_ordering_cronologico(self):
        user = make_user("u_msg_order")
        conv = make_conversation(user)
        m1 = make_message(conv, "user", "Primeira")
        m2 = make_message(conv, "assistant", "Segunda")
        msgs = list(Message.objects.filter(conversation=conv))
        assert msgs[0] == m1
        assert msgs[1] == m2


# ---------------------------------------------------------------------------
# TestConversationSerializer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversationSerializer:
    def test_campos_presentes(self):
        from apps.chat.serializers import ConversationSerializer
        from rest_framework.test import APIRequestFactory

        user = make_user("u_ser_campos")
        conv = make_conversation(user, "Minha Conv")
        request = APIRequestFactory().get("/")
        request.user = user
        data = ConversationSerializer(conv, context={"request": request}).data
        assert data["title"] == "Minha Conv"
        assert "id" in data
        assert "message_count" in data
        assert "last_message" in data
        assert "collection_ids" in data

    def test_message_count(self):
        from apps.chat.serializers import ConversationSerializer
        from rest_framework.test import APIRequestFactory

        user = make_user("u_ser_count")
        conv = make_conversation(user)
        make_message(conv, "user", "A")
        make_message(conv, "assistant", "B")
        request = APIRequestFactory().get("/")
        request.user = user
        data = ConversationSerializer(conv, context={"request": request}).data
        assert data["message_count"] == 2

    def test_last_message_none_sem_mensagens(self):
        from apps.chat.serializers import ConversationSerializer
        from rest_framework.test import APIRequestFactory

        user = make_user("u_ser_last_none")
        conv = make_conversation(user)
        request = APIRequestFactory().get("/")
        request.user = user
        data = ConversationSerializer(conv, context={"request": request}).data
        assert data["last_message"] is None

    def test_collection_ids_vazio(self):
        from apps.chat.serializers import ConversationSerializer
        from rest_framework.test import APIRequestFactory

        user = make_user("u_ser_colids")
        conv = make_conversation(user)
        request = APIRequestFactory().get("/")
        request.user = user
        data = ConversationSerializer(conv, context={"request": request}).data
        assert data["collection_ids"] == []


# ---------------------------------------------------------------------------
# TestConversationCreateSerializer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversationCreateSerializer:
    def test_cria_conversa_simples(self):
        from apps.chat.serializers import ConversationCreateSerializer
        from rest_framework.test import APIRequestFactory

        user = make_user("u_cser_cria")
        request = APIRequestFactory().post("/")
        request.user = user
        ser = ConversationCreateSerializer(
            data={"title": "Nova Consulta", "use_personal_docs": True, "collection_ids": []},
            context={"request": request},
        )
        assert ser.is_valid(), ser.errors
        conv = ser.save()
        assert conv.user == user
        assert conv.title == "Nova Consulta"
        assert conv.use_personal_docs is True

    def test_collection_ids_invalidos_dao_erro(self):
        from apps.chat.serializers import ConversationCreateSerializer
        from rest_framework.test import APIRequestFactory

        user = make_user("u_cser_invalid")
        request = APIRequestFactory().post("/")
        request.user = user
        ser = ConversationCreateSerializer(
            data={"title": "X", "collection_ids": [str(uuid.uuid4())]},
            context={"request": request},
        )
        assert not ser.is_valid()
        assert "collection_ids" in ser.errors

    def test_update_parcial_titulo(self):
        from apps.chat.serializers import ConversationCreateSerializer
        from rest_framework.test import APIRequestFactory

        user = make_user("u_cser_update")
        conv = make_conversation(user, "Antigo")
        request = APIRequestFactory().patch("/")
        request.user = user
        ser = ConversationCreateSerializer(
            conv, data={"title": "Novo Titulo"}, partial=True, context={"request": request}
        )
        assert ser.is_valid(), ser.errors
        updated = ser.save()
        assert updated.title == "Novo Titulo"


# ---------------------------------------------------------------------------
# TestConversationListAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversationListAPI:
    def test_403_anonimo(self):
        client = APIClient()
        url = reverse("chat-api:conversation-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_lista_apenas_proprias(self):
        u1 = make_user("u_list_u1")
        u2 = make_user("u_list_u2")
        make_conversation(u1, "Conv U1")
        make_conversation(u2, "Conv U2")
        client = APIClient()
        client.force_authenticate(u1)
        url = reverse("chat-api:conversation-list")
        response = client.get(url)
        assert response.status_code == 200
        titles = [c["title"] for c in response.data["results"]]
        assert "Conv U1" in titles
        assert "Conv U2" not in titles

    def test_lista_vazia_sem_conversas(self):
        user = make_user("u_list_vazia")
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-list")
        response = client.get(url)
        assert response.status_code == 200
        assert response.data["count"] == 0

    def test_paginacao(self):
        user = make_user("u_list_pag")
        for i in range(5):
            make_conversation(user, f"Conv {i}")
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-list")
        response = client.get(url)
        assert response.status_code == 200
        assert response.data["count"] == 5


# ---------------------------------------------------------------------------
# TestConversationCreateAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversationCreateAPI:
    def test_cria_conversa(self):
        user = make_user("u_create_ok")
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-list")
        response = client.post(url, {"title": "Nova", "use_personal_docs": False, "collection_ids": []})
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["title"] == "Nova"

    def test_403_anonimo(self):
        client = APIClient()
        url = reverse("chat-api:conversation-list")
        response = client.post(url, {"title": "X"})
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_owner_e_usuario_autenticado(self):
        user = make_user("u_create_owner")
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-list")
        client.post(url, {"title": "Minha", "collection_ids": []})
        conv = Conversation.objects.get(user=user, title="Minha")
        assert conv.user == user

    def test_collection_ids_invalido_retorna_400(self):
        user = make_user("u_create_col_inv")
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-list")
        response = client.post(url, {"title": "X", "collection_ids": [str(uuid.uuid4())]})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# TestConversationRetrieveAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversationRetrieveAPI:
    def test_detalhe_proprio(self):
        user = make_user("u_ret_proprio")
        conv = make_conversation(user)
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-detail", kwargs={"pk": conv.id})
        response = client.get(url)
        assert response.status_code == 200
        assert response.data["id"] == str(conv.id)

    def test_outro_usuario_recebe_404(self):
        u1 = make_user("u_ret_u1")
        u2 = make_user("u_ret_u2")
        conv = make_conversation(u1)
        client = APIClient()
        client.force_authenticate(u2)
        url = reverse("chat-api:conversation-detail", kwargs={"pk": conv.id})
        response = client.get(url)
        assert response.status_code == 404

    def test_anonimo_recebe_403(self):
        user = make_user("u_ret_anon")
        conv = make_conversation(user)
        client = APIClient()
        url = reverse("chat-api:conversation-detail", kwargs={"pk": conv.id})
        response = client.get(url)
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# TestConversationUpdateAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversationUpdateAPI:
    def test_patch_titulo(self):
        user = make_user("u_upd_titulo")
        conv = make_conversation(user, "Antigo")
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-detail", kwargs={"pk": conv.id})
        response = client.patch(url, {"title": "Novo Titulo"})
        assert response.status_code == 200
        assert response.data["title"] == "Novo Titulo"

    def test_patch_use_personal_docs(self):
        user = make_user("u_upd_personal")
        conv = make_conversation(user, use_personal_docs=False)
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-detail", kwargs={"pk": conv.id})
        response = client.patch(url, {"use_personal_docs": True})
        assert response.status_code == 200
        conv.refresh_from_db()
        assert conv.use_personal_docs is True

    def test_outro_usuario_nao_pode_atualizar(self):
        u1 = make_user("u_upd_u1")
        u2 = make_user("u_upd_u2")
        conv = make_conversation(u1, "Titulo")
        client = APIClient()
        client.force_authenticate(u2)
        url = reverse("chat-api:conversation-detail", kwargs={"pk": conv.id})
        response = client.patch(url, {"title": "Hackeado"})
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# TestConversationDestroyAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversationDestroyAPI:
    def test_delete_proprio(self):
        user = make_user("u_del_proprio")
        conv = make_conversation(user)
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-detail", kwargs={"pk": conv.id})
        response = client.delete(url)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Conversation.objects.filter(id=conv.id).exists()

    def test_delete_outro_usuario_retorna_404(self):
        u1 = make_user("u_del_u1")
        u2 = make_user("u_del_u2")
        conv = make_conversation(u1)
        client = APIClient()
        client.force_authenticate(u2)
        url = reverse("chat-api:conversation-detail", kwargs={"pk": conv.id})
        response = client.delete(url)
        assert response.status_code == 404
        assert Conversation.objects.filter(id=conv.id).exists()

    def test_delete_cascade_mensagens(self):
        user = make_user("u_del_cascade")
        conv = make_conversation(user)
        make_message(conv, "user", "A")
        make_message(conv, "assistant", "B")
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-detail", kwargs={"pk": conv.id})
        client.delete(url)
        assert Message.objects.filter(conversation=conv).count() == 0


# ---------------------------------------------------------------------------
# TestConversationMessagesAPI
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestConversationMessagesAPI:
    def test_lista_mensagens_em_ordem(self):
        user = make_user("u_msgs_order")
        conv = make_conversation(user)
        make_message(conv, "user", "Primeira")
        make_message(conv, "assistant", "Segunda")
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-messages", kwargs={"pk": conv.id})
        response = client.get(url)
        assert response.status_code == 200
        results = response.data["results"]
        assert results[0]["content"] == "Primeira"
        assert results[1]["content"] == "Segunda"

    def test_403_anonimo(self):
        user = make_user("u_msgs_anon")
        conv = make_conversation(user)
        client = APIClient()
        url = reverse("chat-api:conversation-messages", kwargs={"pk": conv.id})
        response = client.get(url)
        assert response.status_code == 403

    def test_outro_usuario_recebe_404(self):
        u1 = make_user("u_msgs_u1")
        u2 = make_user("u_msgs_u2")
        conv = make_conversation(u1)
        client = APIClient()
        client.force_authenticate(u2)
        url = reverse("chat-api:conversation-messages", kwargs={"pk": conv.id})
        response = client.get(url)
        assert response.status_code == 404

    def test_campos_da_mensagem(self):
        user = make_user("u_msgs_campos")
        conv = make_conversation(user)
        sources = [{"title": "Doc", "id": str(uuid.uuid4()), "type": "knowledge", "index": 1}]
        make_message(conv, "assistant", "Resposta", sources)
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("chat-api:conversation-messages", kwargs={"pk": conv.id})
        response = client.get(url)
        msg = response.data["results"][0]
        assert "id" in msg
        assert "role" in msg
        assert "content" in msg
        assert "sources" in msg
        assert "created_at" in msg


# ---------------------------------------------------------------------------
# TestChatIndexView
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestChatIndexView:
    def test_redireciona_anonimo(self):
        client = Client()
        response = client.get(reverse("chat:index"))
        assert response.status_code == 302
        assert "/oidc/authenticate/" in response["Location"] or "/login" in response["Location"]

    def test_200_autenticado(self):
        user = make_user("u_view_200")
        client = Client()
        client.force_login(user)
        response = client.get(reverse("chat:index"))
        assert response.status_code == 200
        assert b"chat-layout" in response.content

    def test_template_correto(self):
        user = make_user("u_view_tmpl")
        client = Client()
        client.force_login(user)
        response = client.get(reverse("chat:index"))
        templates = [t.name for t in response.templates]
        assert "chat/chat.html" in templates


# ---------------------------------------------------------------------------
# TestHxConversationList
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHxConversationList:
    def test_redireciona_anonimo(self):
        client = Client()
        response = client.get(reverse("chat:hx-conv-list"))
        assert response.status_code == 302

    def test_retorna_lista_html(self):
        user = make_user("u_hx_list")
        make_conversation(user, "Conversa Alpha")
        client = Client()
        client.force_login(user)
        response = client.get(reverse("chat:hx-conv-list"))
        assert response.status_code == 200
        assert b"Conversa Alpha" in response.content

    def test_nao_retorna_conversas_de_outro_usuario(self):
        u1 = make_user("u_hx_list_u1")
        u2 = make_user("u_hx_list_u2")
        make_conversation(u1, "Conv U1 Secreta")
        client = Client()
        client.force_login(u2)
        response = client.get(reverse("chat:hx-conv-list"))
        assert b"Conv U1 Secreta" not in response.content

    def test_estado_vazio(self):
        user = make_user("u_hx_list_vazio")
        client = Client()
        client.force_login(user)
        response = client.get(reverse("chat:hx-conv-list"))
        assert response.status_code == 200
        assert b"Nenhuma conversa" in response.content


# ---------------------------------------------------------------------------
# TestHxConversationDetail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHxConversationDetail:
    def test_detalhe_exibe_mensagens(self):
        user = make_user("u_hx_det")
        conv = make_conversation(user, "Reuniao Semanal")
        make_message(conv, "user", "Qual o resumo da reuniao?")
        make_message(conv, "assistant", "A reuniao foi produtiva.")
        client = Client()
        client.force_login(user)
        response = client.get(reverse("chat:hx-conv-detail", kwargs={"pk": conv.id}))
        assert response.status_code == 200
        assert b"Qual o resumo" in response.content
        assert b"A reuniao foi produtiva" in response.content

    def test_outro_usuario_recebe_404(self):
        u1 = make_user("u_hx_det_u1")
        u2 = make_user("u_hx_det_u2")
        conv = make_conversation(u1)
        client = Client()
        client.force_login(u2)
        response = client.get(reverse("chat:hx-conv-detail", kwargs={"pk": conv.id}))
        assert response.status_code == 404

    def test_contem_ws_init_marker(self):
        user = make_user("u_hx_det_ws")
        conv = make_conversation(user)
        client = Client()
        client.force_login(user)
        response = client.get(reverse("chat:hx-conv-detail", kwargs={"pk": conv.id}))
        assert b"ws-init" in response.content
        assert str(conv.id).encode() in response.content


# ---------------------------------------------------------------------------
# TestHxConversationCreate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHxConversationCreate:
    def test_cria_conversa_retorna_html(self):
        user = make_user("u_hx_cria")
        client = Client()
        client.force_login(user)
        response = client.post(
            reverse("chat:hx-conv-create"),
            {"title": "Nova Via HTMX", "use_personal_docs": ""},
        )
        assert response.status_code == 200
        assert Conversation.objects.filter(user=user, title="Nova Via HTMX").exists()

    def test_retorna_conv_list_oob(self):
        user = make_user("u_hx_cria_oob")
        client = Client()
        client.force_login(user)
        response = client.post(
            reverse("chat:hx-conv-create"),
            {"title": "OOB Test"},
        )
        assert b"conv-list" in response.content
        assert b"hx-swap-oob" in response.content

    def test_titulo_default_se_vazio(self):
        user = make_user("u_hx_cria_def")
        client = Client()
        client.force_login(user)
        client.post(reverse("chat:hx-conv-create"), {"title": ""})
        conv = Conversation.objects.filter(user=user).first()
        assert conv is not None
        assert conv.title == "Nova conversa"

    def test_403_anonimo(self):
        client = Client()
        response = client.post(reverse("chat:hx-conv-create"), {"title": "X"})
        assert response.status_code == 302


# ---------------------------------------------------------------------------
# TestHxConversationDelete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHxConversationDelete:
    def test_deleta_propria_conversa(self):
        user = make_user("u_hx_del")
        conv = make_conversation(user, "Para Deletar")
        client = Client()
        client.force_login(user)
        response = client.post(reverse("chat:hx-conv-delete", kwargs={"pk": conv.id}))
        assert response.status_code == 200
        assert not Conversation.objects.filter(id=conv.id).exists()

    def test_retorna_lista_atualizada_e_empty_state(self):
        user = make_user("u_hx_del_html")
        conv = make_conversation(user)
        client = Client()
        client.force_login(user)
        response = client.post(reverse("chat:hx-conv-delete", kwargs={"pk": conv.id}))
        assert b"conv-list" in response.content
        assert b"hx-swap-oob" in response.content

    def test_outro_usuario_recebe_404(self):
        u1 = make_user("u_hx_del_u1")
        u2 = make_user("u_hx_del_u2")
        conv = make_conversation(u1)
        client = Client()
        client.force_login(u2)
        response = client.post(reverse("chat:hx-conv-delete", kwargs={"pk": conv.id}))
        assert response.status_code == 404
        assert Conversation.objects.filter(id=conv.id).exists()


# ---------------------------------------------------------------------------
# TestHxConversationRename
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHxConversationRename:
    def test_renomeia_conversa(self):
        user = make_user("u_hx_ren")
        conv = make_conversation(user, "Titulo Velho")
        client = Client()
        client.force_login(user)
        response = client.post(
            reverse("chat:hx-conv-rename", kwargs={"pk": conv.id}),
            {"title": "Titulo Novo"},
        )
        assert response.status_code == 200
        conv.refresh_from_db()
        assert conv.title == "Titulo Novo"

    def test_titulo_vazio_nao_altera(self):
        user = make_user("u_hx_ren_vazio")
        conv = make_conversation(user, "Permanece")
        client = Client()
        client.force_login(user)
        client.post(
            reverse("chat:hx-conv-rename", kwargs={"pk": conv.id}),
            {"title": ""},
        )
        conv.refresh_from_db()
        assert conv.title == "Permanece"

    def test_retorna_sidebar_atualizada(self):
        user = make_user("u_hx_ren_html")
        conv = make_conversation(user, "Antigo")
        client = Client()
        client.force_login(user)
        response = client.post(
            reverse("chat:hx-conv-rename", kwargs={"pk": conv.id}),
            {"title": "Novo"},
        )
        assert b"Novo" in response.content


# ---------------------------------------------------------------------------
# TestChatConsumer  (WebSocket — async)
# ---------------------------------------------------------------------------


def _make_ws_app(user=None):
    """Cria app ASGI de teste com usuario injetado no scope (sem sessao/Redis)."""
    from channels.routing import URLRouter
    from django.contrib.auth.models import AnonymousUser

    from apps.chat.routing import websocket_urlpatterns

    inner = URLRouter(websocket_urlpatterns)
    _user = user or AnonymousUser()

    class InjectUser:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            scope = dict(scope)
            scope["user"] = _user
            await self.app(scope, receive, send)

    return InjectUser(inner)


@pytest.mark.django_db(transaction=True)
class TestChatConsumer:
    @pytest.mark.asyncio
    async def test_rejeita_anonimo(self):
        from channels.testing import WebsocketCommunicator

        app = _make_ws_app(user=None)
        url = f"/ws/chat/{uuid.uuid4()}/"
        comm = WebsocketCommunicator(app, url)
        connected, code = await comm.connect()
        assert not connected or code == 4001
        await comm.disconnect()

    @pytest.mark.asyncio
    async def test_rejeita_conversa_inexistente(self):
        from channels.testing import WebsocketCommunicator

        user = await _async_make_user("u_ws_noconv")
        app = _make_ws_app(user=user)
        url = f"/ws/chat/{uuid.uuid4()}/"
        comm = WebsocketCommunicator(app, url)
        connected, code = await comm.connect()
        assert not connected or code == 4003
        await comm.disconnect()

    @pytest.mark.asyncio
    async def test_aceita_conversa_valida(self):
        from channels.testing import WebsocketCommunicator

        user = await _async_make_user("u_ws_accept")
        conv = await _async_make_conversation(user)
        app = _make_ws_app(user=user)
        url = f"/ws/chat/{conv.id}/"
        comm = WebsocketCommunicator(app, url)
        connected, _ = await comm.connect()
        assert connected
        await comm.disconnect()

    @pytest.mark.asyncio
    async def test_recebe_tokens_em_streaming(self):
        from channels.testing import WebsocketCommunicator

        user = await _async_make_user("u_ws_stream")
        conv = await _async_make_conversation(user)

        mock_ctx = MagicMock()
        mock_ctx.sources = []
        mock_ctx.prompt = "Prompt de teste"

        with patch("apps.core.rag_service.RAGService") as MockRAG:
            instance = MockRAG.return_value
            instance.build_context.return_value = mock_ctx
            llm_mock = MagicMock()
            llm_mock.stream.return_value = iter(["Ola", " Mundo"])
            instance._ollama_client.return_value = llm_mock

            app = _make_ws_app(user=user)
            comm = WebsocketCommunicator(app, f"/ws/chat/{conv.id}/")
            connected, _ = await comm.connect()
            assert connected

            await comm.send_json_to({"message": "Teste de streaming"})

            # Coleta tokens
            tokens = []
            for _ in range(5):
                try:
                    msg = await comm.receive_json_from(timeout=3)
                    tokens.append(msg)
                    if msg["type"] in ("done", "error"):
                        break
                except Exception:
                    break

            types = [t["type"] for t in tokens]
            assert "token" in types or "done" in types

            await comm.disconnect()

    @pytest.mark.asyncio
    async def test_mensagem_salva_no_banco(self):
        from channels.db import database_sync_to_async
        from channels.testing import WebsocketCommunicator

        user = await _async_make_user("u_ws_save")
        conv = await _async_make_conversation(user)

        mock_ctx = MagicMock()
        mock_ctx.sources = []
        mock_ctx.prompt = "Prompt"

        with patch("apps.core.rag_service.RAGService") as MockRAG:
            instance = MockRAG.return_value
            instance.build_context.return_value = mock_ctx
            llm_mock = MagicMock()
            llm_mock.stream.return_value = iter(["Resposta"])
            instance._ollama_client.return_value = llm_mock

            app = _make_ws_app(user=user)
            comm = WebsocketCommunicator(app, f"/ws/chat/{conv.id}/")
            await comm.connect()
            await comm.send_json_to({"message": "Pergunta salva?"})

            # Aguarda finalizacao
            for _ in range(5):
                try:
                    msg = await comm.receive_json_from(timeout=3)
                    if msg["type"] in ("done", "error"):
                        break
                except Exception:
                    break

            await comm.disconnect()

        # Verifica mensagens salvas no banco
        count = await database_sync_to_async(
            lambda: Message.objects.filter(conversation=conv).count()
        )()
        assert count >= 1  # Ao menos a mensagem do usuario foi salva


# ---------------------------------------------------------------------------
# Helpers async para TestChatConsumer
# ---------------------------------------------------------------------------


@pytest.fixture
def event_loop():
    """Garante event loop compativel com pytest-asyncio."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def _async_make_user(username: str) -> CustomUser:
    from channels.db import database_sync_to_async

    return await database_sync_to_async(make_user)(username)


async def _async_make_conversation(user: CustomUser) -> Conversation:
    from channels.db import database_sync_to_async

    return await database_sync_to_async(make_conversation)(user, "Conversa WS")
