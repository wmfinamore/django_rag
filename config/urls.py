from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.accounts import views as accounts_views

base_urlpatterns = [
    path("admin/", admin.site.urls),
    path("oidc/", include("mozilla_django_oidc.urls")),
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("api/knowledge/", include("apps.knowledge.urls", namespace="knowledge")),
    path("api/documents/", include("apps.documents.urls", namespace="documents")),
    path("api/chat/", include("apps.chat.api_urls", namespace="chat-api")),
    path("chat/", include("apps.chat.urls", namespace="chat")),
    path("", accounts_views.home, name="home"),
]

if settings.DEBUG:
    import debug_toolbar
    base_urlpatterns = [
        path("__debug__/", include(debug_toolbar.urls)),
    ] + base_urlpatterns

urlpatterns = [
    path("", RedirectView.as_view(url="/rag/", permanent=False)),
    path("rag/", include(base_urlpatterns)),
]
