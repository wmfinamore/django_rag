"""
Modelos da app accounts.

Define o CustomUser, usado como AUTH_USER_MODEL desde o início do projeto,
evitando migrações problemáticas no futuro (conforme arquitetura).
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    """
    Usuário customizado do django_rag.

    Mantém todos os campos padrão do AbstractUser (username, email, first_name,
    last_name, password, is_active, is_staff, is_superuser, groups,
    user_permissions, date_joined, last_login) e adiciona:

    - ``sub``: subject ID do Keycloak (OIDC). Único quando presente;
      pode ficar em branco para contas locais (fallback ModelBackend /admin).
    - ``avatar_url``: campo futuro, URL do avatar do usuário.
    """

    sub = models.CharField(
        "Keycloak subject ID",
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text=(
            "Identificador único do usuário no Keycloak (claim 'sub' do JWT). "
            "Vazio para usuários locais criados via admin."
        ),
        db_comment=(
            "Keycloak subject ID (claim 'sub' do JWT OIDC). Identificador "
            "canônico do usuário quando autenticado via Keycloak. NULL para "
            "contas locais criadas via admin (fallback ModelBackend)."
        ),
    )

    avatar_url = models.CharField(
        "URL do avatar",
        max_length=500,
        blank=True,
        default="",
        help_text="URL pública do avatar do usuário.",
        db_comment="URL pública do avatar do usuário. Vazio quando não definido.",
    )

    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"
        ordering = ("username",)
        db_table_comment = (
            "Usuários da aplicação django_rag. Estende auth.AbstractUser "
            "adicionando o 'sub' do Keycloak (OIDC) e avatar_url. "
            "Registrado como AUTH_USER_MODEL desde a primeira migration."
        )

    def __str__(self) -> str:
        return self.get_full_name() or self.username
