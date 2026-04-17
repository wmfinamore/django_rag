"""
URLs da app accounts.

Rotas locais (perfil, etc.). As rotas OIDC de login/logout/callback são
providas pelo ``mozilla_django_oidc.urls`` e devem ser incluídas em
``config/urls.py`` sob o prefixo ``/oidc/``, conforme arquitetura.
"""

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("profile/", views.profile, name="profile"),
]
