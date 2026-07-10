"""
Views da app chat.

REST API (DRF):
    GET    /api/chat/conversations/                  -- lista conversas do usuário
    POST   /api/chat/conversations/                  -- cria conversa
    GET    /api/chat/conversations/<id>/             -- detalhe da conversa
    PATCH  /api/chat/conversations/<id>/             -- atualiza título / escopo
    DELETE /api/chat/conversations/<id>/             -- remove conversa e mensagens
    GET    /api/chat/conversations/<id>/messages/    -- histórico de mensagens

Views HTML (HTMX):
    GET    /chat/                                    -- página principal do chat
    GET    /chat/hx/conversations/                   -- sidebar: lista de conversas (fragment)
    GET    /chat/hx/conversations/<id>/              -- área principal: detalhes + mensagens (fragment)
    POST   /chat/hx/conversations/new/               -- cria conversa; retorna fragment sidebar + main
    DELETE /chat/hx/conversations/<id>/delete/       -- deleta; retorna fragment sidebar atualizado
    POST   /chat/hx/conversations/<id>/rename/       -- renomeia; retorna fragment sidebar atualizado
    GET    /chat/hx/collections/                     -- retorna opções de coleções para o modal

Controle de acesso:
    Todos os endpoints exigem autenticação.
    Cada usuário acessa apenas suas próprias conversas.
"""

from __future__ import annotations

import logging
import os

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from apps.documents.models import UserDocument
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, mixins

from apps.chat.models import Conversation, Message
from apps.chat.serializers import (
    ConversationCreateSerializer,
    ConversationSerializer,
    MessageSerializer,
)

logger = logging.getLogger(__name__)


class ConversationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """
    ViewSet de Conversation.

    Cada usuário acessa apenas suas próprias conversas.
    PATCH atualiza título e/ou escopo (coleções, use_personal_docs).
    """

    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return (
            Conversation.objects.filter(user=self.request.user)
            .prefetch_related("collections", "messages")
            .order_by("-updated_at")
        )

    def get_serializer_class(self):
        if self.action in ("create", "partial_update"):
            return ConversationCreateSerializer
        return ConversationSerializer

    def get_object(self):
        """Garante que o usuário só acessa suas próprias conversas (404 em vez de 403)."""
        from django.shortcuts import get_object_or_404

        obj = get_object_or_404(Conversation, id=self.kwargs["pk"], user=self.request.user)
        self.check_object_permissions(self.request, obj)
        return obj

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = serializer.save()
        out = ConversationSerializer(conversation, context={"request": request})
        return Response(out.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        conversation = serializer.save()
        out = ConversationSerializer(conversation, context={"request": request})
        return Response(out.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Ação aninhada: histórico de mensagens
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get"], url_path="messages")
    def messages(self, request, pk=None):
        """
        GET /api/chat/conversations/<id>/messages/

        Retorna o histórico completo de mensagens da conversa (paginado).
        """
        conversation = self.get_object()
        qs = Message.objects.filter(conversation=conversation).order_by("created_at")

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = MessageSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = MessageSerializer(qs, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Views HTML — página principal e fragmentos HTMX
# ---------------------------------------------------------------------------

def _accessible_collections(user):
    """Retorna coleções acessíveis pelo usuário (para preencher o modal)."""
    from apps.knowledge.models import KnowledgeCollection
    from django.db.models import Q

    qs = KnowledgeCollection.objects.filter(is_active=True).prefetch_related("allowed_groups")
    if user.is_superuser:
        return qs
    user_group_ids = user.groups.values_list("id", flat=True)
    return qs.filter(Q(allowed_groups__isnull=True) | Q(allowed_groups__in=user_group_ids)).distinct()


@login_required
@require_GET
def chat_index(request):
    """Página principal do chat — shell com sidebar e área de conversa."""
    collections = _accessible_collections(request.user)
    return render(request, "chat/chat.html", {"collections": collections})


@login_required
@require_GET
def hx_conversation_list(request):
    """HTMX: retorna fragment com lista de conversas para a sidebar."""
    conversations = (
        Conversation.objects.filter(user=request.user)
        .prefetch_related("messages")
        .order_by("-updated_at")
    )
    active_id = request.GET.get("active")
    return render(
        request,
        "chat/partials/_sidebar_list.html",
        {"conversations": conversations, "active_id": active_id},
    )


@login_required
@require_GET
def hx_conversation_detail(request, pk):
    """HTMX: retorna fragment com mensagens e input da conversa."""
    conversation = get_object_or_404(Conversation, id=pk, user=request.user)
    messages_qs = Message.objects.filter(conversation=conversation).order_by("created_at")
    return render(
        request,
        "chat/partials/_conversation.html",
        {"conversation": conversation, "messages": messages_qs},
    )


@login_required
@require_POST
def hx_conversation_create(request):
    """HTMX: cria conversa e retorna sidebar atualizada + fragment da nova conversa."""
    title = request.POST.get("title", "").strip() or "Nova conversa"
    use_personal = request.POST.get("use_personal_docs") == "on"
    collection_ids = request.POST.getlist("collection_ids")

    conversation = Conversation.objects.create(
        user=request.user,
        title=title,
        use_personal_docs=use_personal,
    )
    if collection_ids:
        from apps.knowledge.models import KnowledgeCollection
        accessible_ids = _accessible_collections(request.user).filter(
            id__in=collection_ids
        ).values_list("id", flat=True)
        conversation.collections.set(accessible_ids)

    # Fragment da nova conversa (swap principal em #chat-main)
    conversation_html = render(
        request,
        "chat/partials/_conversation.html",
        {"conversation": conversation, "messages": []},
    ).content.decode()

    # Fragment da sidebar atualizado (OOB swap em #conv-list)
    conversations = (
        Conversation.objects.filter(user=request.user)
        .prefetch_related("messages")
        .order_by("-updated_at")
    )
    sidebar_html = render(
        request,
        "chat/partials/_sidebar_list.html",
        {"conversations": conversations, "active_id": str(conversation.id)},
    ).content.decode()

    # Resposta: swap principal + OOB sidebar
    combined = f'{conversation_html}<div id="conv-list" hx-swap-oob="innerHTML">{sidebar_html}</div>'
    return HttpResponse(combined)


@login_required
def hx_conversation_delete(request, pk):
    """HTMX: deleta conversa e retorna sidebar + empty state no main."""
    if request.method not in ("DELETE", "POST"):
        return HttpResponse(status=405)

    conversation = get_object_or_404(Conversation, id=pk, user=request.user)
    conversation.delete()

    conversations = (
        Conversation.objects.filter(user=request.user)
        .prefetch_related("messages")
        .order_by("-updated_at")
    )
    sidebar_html = render(
        request,
        "chat/partials/_sidebar_list.html",
        {"conversations": conversations, "active_id": None},
    ).content.decode()

    empty_html = render(request, "chat/partials/_empty_state.html").content.decode()
    combined = f'{empty_html}<div id="conv-list" hx-swap-oob="innerHTML">{sidebar_html}</div>'
    return HttpResponse(combined)


@login_required
@require_POST
def hx_conversation_rename(request, pk):
    """HTMX: renomeia conversa e retorna fragment do item atualizado na sidebar."""
    conversation = get_object_or_404(Conversation, id=pk, user=request.user)
    new_title = request.POST.get("title", "").strip()
    if new_title:
        conversation.title = new_title
        conversation.save(update_fields=["title", "updated_at"])

    conversations = (
        Conversation.objects.filter(user=request.user)
        .prefetch_related("messages")
        .order_by("-updated_at")
    )
    active_id = request.GET.get("active", str(pk))
    return render(
        request,
        "chat/partials/_sidebar_list.html",
        {"conversations": conversations, "active_id": active_id},
    )


# ---------------------------------------------------------------------------
# Views HTMX — Painel de Documentos Pessoais
# ---------------------------------------------------------------------------

_DOCS_ALLOWED_EXT = {"pdf", "docx", "txt", "md"}
_DOCS_MAX_SIZE = 50 * 1024 * 1024  # 50 MB


def _docs_context(user):
    docs = UserDocument.objects.filter(owner=user).order_by("-created_at")
    has_processing = docs.filter(
        status__in=[UserDocument.Status.PENDING, UserDocument.Status.INDEXING]
    ).exists()
    return {"documents": docs, "has_processing": has_processing}


@login_required
@require_GET
def hx_documents_panel(request):
    """HTMX: painel completo de documentos (form + lista)."""
    return render(request, "chat/partials/_docs_panel.html", _docs_context(request.user))


@login_required
@require_GET
def hx_documents_list(request):
    """HTMX: apenas a lista de documentos (usada pelo polling automático)."""
    return render(request, "chat/partials/_docs_list.html", _docs_context(request.user))


@login_required
@require_POST
def hx_document_upload(request):
    """HTMX: faz upload de documento e dispara indexação via Celery."""
    file_obj = request.FILES.get("file")
    title = request.POST.get("title", "").strip()
    upload_error = None

    if not file_obj:
        upload_error = "Selecione um arquivo."
    else:
        ext = os.path.splitext(file_obj.name)[1].lstrip(".").lower()
        if ext not in _DOCS_ALLOWED_EXT:
            upload_error = (
                f"Formato não suportado. Use: {', '.join(sorted(_DOCS_ALLOWED_EXT))}."
            )
        elif file_obj.size > _DOCS_MAX_SIZE:
            upload_error = "Arquivo muito grande (máx. 50 MB)."

    if not upload_error:
        ext = os.path.splitext(file_obj.name)[1].lstrip(".").lower()
        doc = UserDocument(
            owner=request.user,
            title=title or os.path.splitext(file_obj.name)[0],
            file_type=ext,
            status=UserDocument.Status.PENDING,
        )
        doc.file.save(file_obj.name, file_obj, save=False)
        doc.save()
        try:
            doc.trigger_indexing()
        except Exception:
            logger.warning("Falha ao enfileirar indexação para doc %s", doc.id)

    ctx = _docs_context(request.user)
    if upload_error:
        ctx["upload_error"] = upload_error
    return render(request, "chat/partials/_docs_panel.html", ctx)


@login_required
@require_POST
def hx_document_delete(request, pk):
    """HTMX: remove documento via Celery e atualiza o painel (UI otimista)."""
    doc = UserDocument.objects.filter(pk=pk, owner=request.user).first()
    if doc is None:
        # Documento já removido (ou de outro usuário): painel estava
        # desatualizado — apenas re-renderiza com o estado atual.
        return render(
            request, "chat/partials/_docs_panel.html", _docs_context(request.user)
        )

    from apps.core.tasks import delete_document
    try:
        delete_document.delay(str(doc.id), "personal")
    except Exception:
        logger.warning("Falha ao enfileirar deleção para doc %s — removendo sincronamente", doc.id)
        doc.chunks.all().delete()
        if doc.file:
            try:
                doc.file.delete(save=False)
            except Exception:
                pass
        doc.delete()

    # Resposta otimista: omite o documento antes de o Celery processar
    docs = UserDocument.objects.filter(owner=request.user).exclude(pk=pk).order_by("-created_at")
    has_processing = docs.filter(
        status__in=[UserDocument.Status.PENDING, UserDocument.Status.INDEXING]
    ).exists()
    return render(request, "chat/partials/_docs_panel.html", {
        "documents": docs,
        "has_processing": has_processing,
    })


@login_required
@require_POST
def hx_document_reindex(request, pk):
    """HTMX: re-indexa documento e atualiza o painel."""
    doc = UserDocument.objects.filter(pk=pk, owner=request.user).first()
    if doc is None:
        # Documento não existe mais (ou é de outro usuário): re-renderiza
        # o painel em vez de devolver 404 e deixar a UI presa no estado velho.
        return render(
            request, "chat/partials/_docs_panel.html", _docs_context(request.user)
        )
    if doc.status != UserDocument.Status.INDEXING:
        try:
            doc.trigger_reindex()
        except Exception:
            logger.warning("Falha ao enfileirar re-indexação para doc %s", doc.id)
    return render(request, "chat/partials/_docs_panel.html", _docs_context(request.user))
