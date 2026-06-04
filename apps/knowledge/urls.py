"""
URLs da app knowledge.

Roteamento via DefaultRouter do DRF:

    /api/knowledge/collections/                              GET, POST
    /api/knowledge/collections/<id>/                         GET
    /api/knowledge/collections/<id>/documents/               GET, POST
    /api/knowledge/collections/<id>/bulk-import/             GET, POST  (staff only)
    /api/knowledge/collections/<id>/bulk-import/<job_id>/    GET        (staff only)
    /api/knowledge/documents/<id>/                           GET, DELETE
    /api/knowledge/documents/<id>/reindex/                   POST
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.knowledge.views import KnowledgeCollectionViewSet, KnowledgeDocumentViewSet

app_name = "knowledge"

router = DefaultRouter()
router.register(r"collections", KnowledgeCollectionViewSet, basename="collection")
router.register(r"documents", KnowledgeDocumentViewSet, basename="document")

urlpatterns = [
    path("", include(router.urls)),
]
