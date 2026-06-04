"""
Views REST da app knowledge.

Endpoints:
    GET    /api/knowledge/collections/                           -- lista colecoes acessiveis
    POST   /api/knowledge/collections/                           -- cria colecao (staff)
    GET    /api/knowledge/collections/<id>/                      -- detalhe de uma colecao
    GET    /api/knowledge/collections/<id>/documents/            -- lista documentos
    POST   /api/knowledge/collections/<id>/documents/            -- upload + indexacao
    GET    /api/knowledge/collections/<id>/bulk-import/          -- lista BulkImportJobs (staff)
    POST   /api/knowledge/collections/<id>/bulk-import/          -- dispara importacao em lote (staff)
    GET    /api/knowledge/collections/<id>/bulk-import/<job_id>/ -- detalhe de um job (staff)
    GET    /api/knowledge/documents/<id>/                        -- detalhe do documento
    DELETE /api/knowledge/documents/<id>/                        -- remove documento (staff)
    POST   /api/knowledge/documents/<id>/reindex/                -- re-indexa documento (staff)
"""

from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, mixins

from apps.knowledge.models import BulkImportJob, KnowledgeCollection, KnowledgeDocument
from apps.knowledge.serializers import (
    BulkImportJobCreateSerializer,
    BulkImportJobSerializer,
    KnowledgeCollectionSerializer,
    KnowledgeDocumentSerializer,
    KnowledgeDocumentUploadSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class IsStaffOrReadOnly(permissions.BasePermission):
    """Staff pode escrever; usuarios autenticados tem apenas leitura."""

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
    """ViewSet de KnowledgeCollection com suporte a upload e bulk import."""

    serializer_class = KnowledgeCollectionSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        user = self.request.user
        qs = KnowledgeCollection.objects.filter(is_active=True).prefetch_related(
            "allowed_groups", "documents"
        )
        if user.is_superuser:
            return qs
        from django.db.models import Q
        return qs.filter(
            Q(allowed_groups__isnull=True) | Q(allowed_groups__in=user.groups.all())
        ).distinct()

    def retrieve(self, request, *args, **kwargs):
        instance = get_object_or_404(KnowledgeCollection, pk=kwargs["pk"])
        if not instance.is_accessible_by(request.user):
            return Response(
                {"detail": "Voce nao tem acesso a esta colecao."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    # ------------------------------------------------------------------
    # Action: documents
    # ------------------------------------------------------------------

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="documents",
        parser_classes=[MultiPartParser, FormParser],
    )
    def documents(self, request, pk=None):
        """GET -> lista documentos. POST -> upload + indexacao."""
        collection = get_object_or_404(KnowledgeCollection, pk=pk, is_active=True)
        if not collection.is_accessible_by(request.user):
            return Response(
                {"detail": "Voce nao tem acesso a esta colecao."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if request.method == "GET":
            return self._list_documents(request, collection)
        return self._upload_document(request, collection)

    def _list_documents(self, request, collection):
        qs = collection.documents.all()
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        serializer = KnowledgeDocumentSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    def _upload_document(self, request, collection):
        if not request.user.is_staff:
            return Response(
                {"detail": "Apenas staff pode enviar documentos."},
                status=status.HTTP_403_FORBIDDEN,
            )
        data = {
            "title": request.data.get("title", ""),
            "file": request.FILES.get("file"),
            "collection_id": str(collection.id),
        }
        serializer = KnowledgeDocumentUploadSerializer(data=data, context={"request": request})
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

        try:
            task_id = doc.trigger_indexing()
            logger.info("Indexacao enfileirada: doc_id=%s task_id=%s", doc.id, task_id)
        except Exception as exc:
            logger.warning("Falha ao enfileirar indexacao para doc %s: %s", doc.id, exc)
            task_id = None

        response_data = KnowledgeDocumentSerializer(doc, context={"request": request}).data
        response_data["task_id"] = task_id
        return Response(response_data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Action: bulk-import
    # ------------------------------------------------------------------

    @action(
        detail=True,
        methods=["get", "post"],
        url_path=r"bulk-import(?:/(?P<job_id>[0-9a-f-]+))?",
        permission_classes=[permissions.IsAdminUser],
    )
    def bulk_import(self, request, pk=None, job_id=None):
        """
        GET  /collections/<id>/bulk-import/          -> lista BulkImportJobs.
        POST /collections/<id>/bulk-import/          -> dispara novo job.
        GET  /collections/<id>/bulk-import/<job_id>/ -> detalhe de um job.
        """
        collection = get_object_or_404(KnowledgeCollection, pk=pk, is_active=True)
        if job_id:
            return self._bulk_import_detail(request, collection, job_id)
        if request.method == "GET":
            return self._bulk_import_list(request, collection)
        return self._bulk_import_create(request, collection)

    def _bulk_import_list(self, request, collection):
        qs = collection.bulk_import_jobs.select_related("triggered_by").order_by("-created_at")
        serializer = BulkImportJobSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    def _bulk_import_detail(self, request, collection, job_id):
        job = get_object_or_404(BulkImportJob, pk=job_id, collection=collection)
        serializer = BulkImportJobSerializer(job, context={"request": request})
        return Response(serializer.data)

    def _bulk_import_create(self, request, collection):
        """
        Cria um BulkImportJob e dispara run_bulk_import via Celery.

        Body JSON esperado:
            {
                "source_directory": "/mnt/documentos/rh",
                "recursive": true,
                "file_extensions": ["pdf", "docx"]
            }
        """
        serializer = BulkImportJobCreateSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data
        job = BulkImportJob.objects.create(
            collection=collection,
            source_directory=validated["source_directory"],
            recursive=validated["recursive"],
            file_extensions=validated["file_extensions"],
            status=BulkImportJob.Status.PENDING,
            triggered_by=request.user,
        )

        try:
            from apps.knowledge.tasks import run_bulk_import
            task = run_bulk_import.delay(str(job.id))
            BulkImportJob.objects.filter(pk=job.pk).update(celery_task_id=task.id)
            logger.info(
                "BulkImportJob %s enfileirado: collection=%s dir=%s task=%s",
                job.id, collection.id, job.source_directory, task.id,
            )
        except Exception as exc:
            logger.exception("Falha ao enfileirar BulkImportJob %s: %s", job.id, exc)
            job.status = BulkImportJob.Status.FAILED
            job.error_message = f"Falha ao enfileirar task Celery: {exc}"
            job.save(update_fields=["status", "error_message"])
            return Response(
                {"detail": f"Job criado mas falha ao enfileirar: {exc}", "job_id": str(job.id)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        response_data = BulkImportJobSerializer(job, context={"request": request}).data
        return Response(response_data, status=status.HTTP_202_ACCEPTED)


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

    GET    /documents/<id>/         -> detalhe (verifica acesso a colecao).
    DELETE /documents/<id>/         -> remove documento e chunks (staff).
    POST   /documents/<id>/reindex/ -> re-indexa o documento (admin).
    """

    serializer_class = KnowledgeDocumentSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        return KnowledgeDocument.objects.select_related(
            "collection", "ingested_by", "bulk_import_job"
        )

    def retrieve(self, request, *args, **kwargs):
        doc = get_object_or_404(KnowledgeDocument, pk=kwargs["pk"])
        if not doc.collection.is_accessible_by(request.user):
            return Response(
                {"detail": "Voce nao tem acesso a este documento."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(doc)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        if not request.user.is_staff:
            return Response(
                {"detail": "Apenas staff pode remover documentos."},
                status=status.HTTP_403_FORBIDDEN,
            )
        doc = get_object_or_404(KnowledgeDocument, pk=kwargs["pk"])
        if not doc.collection.is_accessible_by(request.user):
            return Response(
                {"detail": "Voce nao tem acesso a este documento."},
                status=status.HTTP_403_FORBIDDEN,
            )
        from apps.core.tasks import delete_document
        try:
            delete_document.delay(str(doc.id), "knowledge")
        except Exception as exc:
            logger.warning("Falha ao enfileirar delecao para doc %s: %s", doc.id, exc)
            doc.chunks.all().delete()
            doc.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(
            {"detail": "Remocao enfileirada.", "doc_id": str(doc.id)},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="reindex",
        permission_classes=[permissions.IsAdminUser],
    )
    def reindex(self, request, pk=None):
        """Re-indexa o documento (delete + index via Celery chain). Somente admin."""
        doc = get_object_or_404(KnowledgeDocument, pk=pk)
        if not doc.collection.is_accessible_by(request.user):
            return Response(
                {"detail": "Voce nao tem acesso a este documento."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if doc.status == KnowledgeDocument.Status.INDEXING:
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
