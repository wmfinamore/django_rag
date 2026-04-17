"""
Forms da app accounts.

Subclasses mínimas dos forms padrão do Django para trabalhar com o CustomUser
no admin. Quando o CustomUser estiver ativo, os forms built-in (que apontam
para auth.User) não podem mais ser usados.
"""

from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from apps.accounts.models import CustomUser


class CustomUserCreationForm(UserCreationForm):
    """Form de criação de usuário (admin)."""

    class Meta(UserCreationForm.Meta):
        model = CustomUser
        fields = ("username", "email")


class CustomUserChangeForm(UserChangeForm):
    """Form de edição de usuário (admin)."""

    class Meta(UserChangeForm.Meta):
        model = CustomUser
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
            "sub",
            "avatar_url",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        )
