"""
Views da app accounts.

Por enquanto apenas uma view de perfil. Login/logout serão providos pelo
``mozilla_django_oidc.urls`` (rotas ``/oidc/authenticate/``, ``/oidc/callback/``,
``/oidc/logout/``), incluídas em ``config/urls.py``.
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


@login_required
def profile(request: HttpRequest) -> HttpResponse:
    """Exibe dados do usuário logado (sub, grupos, nome)."""
    return render(request, "accounts/profile.html", {"user": request.user})
