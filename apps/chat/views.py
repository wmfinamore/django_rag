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

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST
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
