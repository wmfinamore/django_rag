"""
Views REST da app knowledge.

Endpoints disponíveis:
    GET  /api/knowledge/collections/               — lista coleções acessíveis ao usuário
    POST /api/knowledge/collections/               — cria nova coleção (staff only)
    GET  /api/knowledge/collections/<id>/          — detalhe de uma coleção
    GET  /api/knowledge/collections/<id>/documents/— lista documentos da coleção
    POST /api/knowledge/collections/<id>/documents/— faz upload e enfileira indexação
    GET  /api/knowledge/documents/<id>/            — detalhe do documento (status, chunks)
    DELETE /api/knowledge/documents/<id>/          — remove documento e chunks (staff only)
    POST /api/knowledge/documents/<id>/reindex/    — re-indexa documento (staff only)
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, mixins

from apps.knowledge.models import KnowledgeCollection, KnowledgeDocument
from apps.knowledge.serializers import (
    KnowledgeCollectionSerializer,
    KnowledgeDocumentSerializer,
    KnowledgeDocumentUploadSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Permissions customizadas
# ---------------------------------------------------------------------------


class IsStaffOrReadOnly(permissions.BasePermission):
    """
    Staff (is_staff=True) pode criar/editar/deletar.
    Demais usuários autenticados têm apenas leitura.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_staff


# ---------------------------------------------------------------------------
# KnowledgeCollectionViewSet
# ---------------------------------------------------------------------------


class KnowledgeCollectionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    GenericViewSet,
):
    """
    ViewSet de KnowledgeCollection.

    - GET  /collections/       → lista coleções acessíveis ao usuário autenticado.
    - GET  /collections/<id>/  → detalhe de uma coleção (se acessível).
    - POST /collections/       → cria nova coleção (staff only).
    - GET  /collections/<id>/documents/ → lista documentos da coleção.
    - POST /collections/<id>/documents/ → upload e enfileiramento de indexação.
    """

    serializer_class = KnowledgeCollectionSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        """
        Retorna apenas as coleções ativas acessíveis ao usuário.
        Superusuários veem todas. Demais filtram por grupo.
        """
        user = self.request.user
        qs = KnowledgeCollection.objects.filter(is_active=True).prefetch_related(
            "allowed_groups", "documents"
        )
        if user.is_superuser:
            return qs
        # Coleções sem grupos restritos (públicas) OU onde o usuário está no grupo
        from django.db.models import Q
        return qs.filter(
            Q(allowed_groups__isnull=True) | Q(allowed_groups__in=user.groups.all())
        ).distinct()

    def retrieve(self, request, *args, **kwargs):
        """Detalhe de uma coleção — verifica acesso."""
        instance = get_object_or_404(KnowledgeCollection, pk=kwargs["pk"])
        if not instance.is_accessible_by(request.user):
            return Response(
                {"detail": "Você não tem acesso a esta coleção."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="documents",
        parser_classes=[MultiPartParser, FormParser],
    )
    def documents(self, request, pk=None):
        """
        GET  → lista documentos da coleção (filtrado por status opcionalmente).
        POST → upload de um novo documento e enfileiramento de indexação.
        """
        collection = get_object_or_404(KnowledgeCollection, pk=pk, is_active=True)

        if not collection.is_accessible_by(request.user):
            return Response(
                {"detail": "Você não tem acesso a esta coleção."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.method == "GET":
            return self._list_documents(request, collection)
        return self._upload_document(request, collection)

    def _list_documents(self, request, collection):
        """Lista os documentos de uma coleção com filtro opcional por status."""
        qs = collection.documents.all()
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        serializer = KnowledgeDocumentSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    def _upload_document(self, request, collection):
        """Faz upload do arquivo, cria KnowledgeDocument e enfileira indexação."""
        if not request.user.is_staff:
            return Response(
                {"detail": "Apenas staff pode enviar documentos."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Monta o payload manualmente para que o arquivo (request.FILES)
        # seja incluído — {**request.data} num QueryDict multipart só copia
        # os campos de texto; os arquivos ficam em request.FILES separados.
        data = {
            "title": request.data.get("title", ""),
            "file": request.FILES.get("file"),
            "collection_id": str(collection.id),
        }
        serializer = KnowledgeDocumentUploadSerializer(
            data=data,
            context={"request": request},
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            file_path, file_type = serializer.save_file()
        except Exception as exc:
            logger.exception("Erro ao salvar arquivo: %s", exc)
            return Response(
                {"detail": f"Falha ao salvar o arquivo: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        doc = KnowledgeDocument.objects.create(
            collection=collection,
            title=serializer.validated_data["title"],
            file_path=file_path,
            file_type=file_type,
            status=KnowledgeDocument.Status.PENDING,
            ingested_by=request.user,
        )

        # Enfileira indexação Celery
        try:
            task_id = doc.trigger_indexing()
            logger.info("Indexação enfileirada: doc_id=%s task_id=%s", doc.id, task_id)
        except Exception as exc:
            logger.warning("Falha ao enfileirar indexação para doc %s: %s", doc.id, exc)
            task_id = None

        response_data = KnowledgeDocumentSerializer(doc, context={"request": request}).data
        response_data["task_id"] = task_id
        return Response(response_data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# KnowledgeDocumentViewSet
# ---------------------------------------------------------------------------


class KnowledgeDocumentViewSet(
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """
    ViewSet de KnowledgeDocument.

    - GET    /documents/<id>/         → detalhe do documento (verifica acesso à coleção).
    - DELETE /documents/<id>/         → remove documento e chunks (staff only).
    - POST   /documents/<id>/reindex/ → re-indexa o documento (staff only).
    """

    serializer_class = KnowledgeDocumentSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        return KnowledgeDocument.objects.select_related("collection", "ingested_by")

    def retrieve(self, request, *args, **kwargs):
        """Detalhe do documento — verifica acesso à coleção."""
        doc = get_object_or_404(KnowledgeDocument, pk=kwargs["pk"])
        if not doc.collection.is_accessible_by(request.user):
            return Response(
                {"detail": "Você não tem acesso a este documento."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(doc)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        """Remove o documento e enfileira a deleção dos chunks no Celery."""
        if not request.user.is_staff:
            return Response(
                {"detail": "Apenas staff pode remover documentos."},
                status=status.HTTP_403_FORBIDDEN,
            )
        doc = get_object_or_404(KnowledgeDocument, pk=kwargs["pk"])
        if not doc.collection.is_accessible_by(request.user):
            return Response(
                {"detail": "Você não tem acesso a este documento."},
                status=status.HTTP_403_FORBIDDEN,
            )

        from apps.core.tasks import delete_document
        try:
            delete_document.delay(str(doc.id), "knowledge")
        except Exception as exc:
            logger.warning("Falha ao enfileirar deleção para doc %s: %s", doc.id, exc)
            # Deleta diretamente se Celery não estiver disponível
            doc.chunks.all().delete()
            doc.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(
            {"detail": "Remoção enfileirada.", "doc_id": str(doc.id)},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"], url_path="reindex", permission_classes=[permissions.IsAdminUser])
    def reindex(self, request, pk=None):
        """
        Re-indexa o documento (delete + index via Celery chain).
        Somente staff/admin.
        """
        doc = get_object_or_404(KnowledgeDocument, pk=pk)
        if not doc.collection.is_accessible_by(request.user):
            return Response(
                {"detail": "Você não tem acesso a este documento."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if doc.status == KnowledgeDocument.Status.INDEXING:
            return Response(
                {"detail": "O documento já está sendo indexado."},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            task_id = doc.trigger_reindex()
        except Exception as exc:
            logger.exception("Falha ao enfileirar re-indexação para doc %s: %s", doc.id, exc)
            return Response(
                {"detail": f"Falha ao enfileirar re-indexação: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"detail": "Re-indexação enfileirada.", "doc_id": str(doc.id), "task_id": task_id},
            status=status.HTTP_202_ACCEPTED,
        )
