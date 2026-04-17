"""
Views da app accounts.

- home: landing page pública; exibe dados básicos se o usuário já estiver
  autenticado, caso contrário oferece botão de login via OIDC.
- profile: página protegida com informações detalhadas do usuário (sub, grupos).
- keycloak_logout: destrói a sessão Django E encerra a sessão no Keycloak
  (chama o end_session_endpoint com id_token_hint).

As rotas OIDC de login/callback são providas pelo
``mozilla_django_oidc.urls`` e ficam sob ``/rag/oidc/``.
"""

import urllib.parse

from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render


def home(request: HttpRequest) -> HttpResponse:
    """Landing page pública.

    Usuários autenticados vêem uma saudação; anônimos vêem o botão de login.
    """
    return render(request, "home.html")


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    """Exibe dados detalhados do usuário logado (sub, grupos, nome)."""
    groups = request.user.groups.values_list("name", flat=True)
    return render(
        request,
        "accounts/profile.html",
        {"user": request.user, "groups": groups},
    )


def keycloak_logout(request: HttpRequest) -> HttpResponse:
    """Encerra a sessão Django e redireciona para o logout do Keycloak.

    O Keycloak invalida a sessão SSO ao receber o id_token_hint.
    Após o logout no Keycloak, o usuário é redirecionado de volta para home.
    """
    logout_endpoint = getattr(settings, "OIDC_OP_LOGOUT_ENDPOINT", "")
    post_logout_uri = request.build_absolute_uri("/rag/")

    # Lê o id_token ANTES de destruir a sessão
    id_token = request.session.get("oidc_id_token")

    # Destrói a sessão Django
    django_logout(request)

    if logout_endpoint and id_token:
        params = {
            "id_token_hint": id_token,
            "post_logout_redirect_uri": post_logout_uri,
        }
        return redirect(f"{logout_endpoint}?{urllib.parse.urlencode(params)}")

    # Fallback: sem token ou sem endpoint, vai direto para home
    return redirect(post_logout_uri)
