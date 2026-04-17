"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.accounts import views as accounts_views

base_urlpatterns = [
    path('admin/', admin.site.urls),
    # OIDC — login, callback e logout via Keycloak
    path('oidc/', include('mozilla_django_oidc.urls')),
    # App accounts — perfil e outras rotas locais
    path('accounts/', include('apps.accounts.urls', namespace='accounts')),
    # Home
    path('', accounts_views.home, name='home'),
]

if settings.DEBUG:
    import debug_toolbar

    base_urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
    ] + base_urlpatterns

urlpatterns = [
    # Redireciona a raiz "/" para "/rag/"
    path('', RedirectView.as_view(url='/rag/', permanent=False)),
    path('rag/', include(base_urlpatterns)),
]
