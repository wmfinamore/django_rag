"""
Forms da app accounts.

Subclasses mínimas dos forms padrão do Django para trabalhar com o CustomUser
no admin. Quando o CustomUser estiver ativo, os forms built-in (que apontam
para auth.User) não podem mais ser usados.
"""

from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from apps.accounts.models import CustomUser


class CustomUserCreationForm(AdminUserCreationForm):
    """Form de criação de usuário (admin).

    Herda de AdminUserCreationForm (e não de UserCreationForm) para preservar
    o campo ``usable_password`` exigido pelo add_fieldsets do UserAdmin.
    """

    class Meta(AdminUserCreationForm.Meta):
        model = CustomUser
        fields = ("username", "email")


class CustomUserChangeForm(UserChangeForm):
    """Form de edição de usuário (admin)."""

    class Meta(UserChangeForm.Meta):
        model = CustomUser
        fields = (
            "username",
            "password",
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
