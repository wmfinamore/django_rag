"""
Views REST da app documents.

Endpoints (todos exigem autenticacao; cada usuario ve apenas seus proprios docs):

    GET    /api/documents/               -- lista documentos do usuario autenticado
    POST   /api/documents/               -- upload de novo documento + indexacao assincrona
    GET    /api/documents/<id>/          -- detalhe de um documento
    DELETE /api/documents/<id>/          -- remove documento e chunks (assincrono via Celery)
    POST   /api/documents/<id>/reindex/  -- re-indexa o documento (Celery chain delete+index)
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, mixins

from apps.documents.models import UserDocument
from apps.documents.serializers import UserDocumentSerializer, UserDocumentUploadSerializer

logger = logging.getLogger(__name__)


class UserDocumentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    GenericViewSet,
):
    """
    ViewSet de UserDocument.

    Todos os endpoints exigem autenticacao.
    Cada usuario acessa apenas seus proprios documentos.

    GET    /documents/               -> lista documentos do usuario autenticado.
    POST   /documents/               -> upload + indexacao assincrona.
    GET    /documents/<id>/          -> detalhe do documento.
    DELETE /documents/<id>/          -> remove documento e chunks (202 Accepted).
    POST   /documents/<id>/reindex/  -> re-indexa o documento (202 Accepted).
    """

    serializer_class = UserDocumentSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        """Retorna apenas os documentos do usuario autenticado."""
        return UserDocument.objects.filter(owner=self.request.user).order_by("-created_at")

    def get_object(self):
        """Garante que o usuario so acessa seus proprios documentos."""
        qs = self.get_queryset()
        doc = get_object_or_404(qs, pk=self.kwargs["pk"])
        self.check_object_permissions(self.request, doc)
        return doc

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def create(self, request, *args, **kwargs):
        """POST /documents/ — faz upload do arquivo e dispara indexacao."""
        serializer = UserDocumentUploadSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        file_obj = serializer.validated_data["file"]
        ext = os.path.splitext(file_obj.name)[1].lstrip(".").lower()

        doc = UserDocument(
            owner=request.user,
            title=serializer.validated_data["title"],
            file_type=ext,
            status=UserDocument.Status.PENDING,
        )
        # Salva o arquivo via FileField (usa o upload_to do modelo)
        doc.file.save(file_obj.name, file_obj, save=False)
        doc.save()

        try:
            task_id = doc.trigger_indexing()
            logger.info("Indexacao enfileirada: doc_id=%s task_id=%s", doc.id, task_id)
        except Exception as exc:
            logger.warning("Falha ao enfileirar indexacao para doc %s: %s", doc.id, exc)
            task_id = None

        response_data = UserDocumentSerializer(doc, context={"request": request}).data
        response_data["task_id"] = task_id
        return Response(response_data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def destroy(self, request, *args, **kwargs):
        """DELETE /documents/<id>/ — remove documento e chunks via Celery (202)."""
        doc = self.get_object()

        from apps.core.tasks import delete_document
        try:
            delete_document.delay(str(doc.id), "personal")
        except Exception as exc:
            logger.warning("Falha ao enfileirar delecao para doc %s: %s", doc.id, exc)
            # Fallback sincrono: remove chunks e documento diretamente
            doc.chunks.all().delete()
            if doc.file:
                try:
                    doc.file.delete(save=False)
                except Exception:
                    pass
            doc.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(
            {"detail": "Remocao enfileirada.", "doc_id": str(doc.id)},
            status=status.HTTP_202_ACCEPTED,
        )

    # ------------------------------------------------------------------
    # Reindex
    # ------------------------------------------------------------------

    @action(
        detail=True,
        methods=["post"],
        url_path="reindex",
        permission_classes=[permissions.IsAuthenticated],
    )
    def reindex(self, request, pk=None):
        """POST /documents/<id>/reindex/ — re-indexa o documento (delete + index)."""
        doc = self.get_object()

        if doc.status == UserDocument.Status.INDEXING:
            return Response(
                {"detail": "O documento ja esta sendo indexado."},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            task_id = doc.trigger_reindex()
        except Exception as exc:
            logger.exception("Falha ao enfileirar re-indexacao para doc %s: %s", doc.id, exc)
            return Response(
                {"detail": f"Falha ao enfileirar re-indexacao: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"detail": "Re-indexacao enfileirada.", "doc_id": str(doc.id), "task_id": task_id},
            status=status.HTTP_202_ACCEPTED,
        )
