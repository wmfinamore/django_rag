"""
URLs da app documents.

Roteamento via DefaultRouter do DRF:

    /api/documents/               GET (lista), POST (upload)
    /api/documents/<id>/          GET (detalhe), DELETE (remove)
    /api/documents/<id>/reindex/  POST (re-indexa)
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.documents.views import UserDocumentViewSet

app_name = "documents"

router = DefaultRouter()
router.register(r"", UserDocumentViewSet, basename="document")

urlpatterns = [
    path("", include(router.urls)),
]
