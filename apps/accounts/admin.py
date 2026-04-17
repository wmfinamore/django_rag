"""
Admin da app accounts.

Registra o CustomUser reaproveitando o UserAdmin padrão e adicionando
os campos ``sub`` e ``avatar_url`` na seção de informações pessoais.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from apps.accounts.forms import CustomUserChangeForm, CustomUserCreationForm
from apps.accounts.models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    """Admin do CustomUser, estendendo o UserAdmin padrão."""

    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    model = CustomUser

    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "is_active",
        "sub",
    )
    list_filter = ("is_staff", "is_superuser", "is_active", "groups")
    search_fields = ("username", "email", "first_name", "last_name", "sub")
    ordering = ("username",)

    # Copia os fieldsets padrão e insere uma seção "OIDC / Perfil" com os
    # campos customizados. Mantém pessoais, permissões e datas intactos.
    fieldsets = UserAdmin.fieldsets + (
        (
            _("OIDC / Perfil"),
            {"fields": ("sub", "avatar_url")},
        ),
    )
