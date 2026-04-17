"""
Backend OIDC customizado para integração com Keycloak.

Responsabilidades:
    - Usar ``sub`` (Keycloak subject) como identificador canônico do usuário,
      em vez do e-mail (que pode mudar).
    - Sincronizar os grupos do usuário a partir do claim ``groups`` do JWT:
      Group.objects.get_or_create() para cada grupo e user.groups.set(...).

"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from mozilla_django_oidc.auth import OIDCAuthenticationBackend

UserModel = get_user_model()


class GroupSyncOIDCBackend(OIDCAuthenticationBackend):
    """
    Backend OIDC que:
      - Identifica o usuário pelo claim ``sub`` (Keycloak subject ID).
      - Sincroniza os grupos do Django a partir do claim ``groups`` do token.

    TODO (quando mozilla-django-oidc estiver instalado e Keycloak configurado):
      - Validar mapeamento do claim "groups" no realm django-rag.
      - Implementar tratamento de grupos hierárquicos (Keycloak pode devolver
        com prefixo "/"), fazendo strip do prefixo antes do get_or_create.
      - Decidir política para grupos que não existem no Keycloak mas existem
        no Django (remover do usuário vs. preservar).
    """

    # ------------------------------------------------------------------ #
    # Lookup pelo sub em vez de e-mail                                   #
    # ------------------------------------------------------------------ #

    def filter_users_by_claims(self, claims: dict[str, Any]):
        """Procura o usuário pelo ``sub`` do token."""
        sub = claims.get("sub")
        if not sub:
            return UserModel.objects.none()
        return UserModel.objects.filter(sub=sub)

    # ------------------------------------------------------------------ #
    # Criação / atualização                                              #
    # ------------------------------------------------------------------ #

    def create_user(self, claims: dict[str, Any]):
        """Cria um novo usuário a partir dos claims do JWT."""
        user = UserModel.objects.create(
            username=claims.get("preferred_username") or claims["sub"],
            email=claims.get("email", ""),
            first_name=claims.get("given_name", ""),
            last_name=claims.get("family_name", ""),
            sub=claims["sub"],
        )
        self._sync_groups(user, claims)
        return user

    def update_user(self, user, claims: dict[str, Any]):
        """Atualiza dados básicos e resincroniza os grupos."""
        user.email = claims.get("email", user.email)
        user.first_name = claims.get("given_name", user.first_name)
        user.last_name = claims.get("family_name", user.last_name)
        # Garante que o sub está gravado (usuário pré-existente migrado).
        if not user.sub:
            user.sub = claims.get("sub", "")
        user.save(update_fields=["email", "first_name", "last_name", "sub"])
        self._sync_groups(user, claims)
        return user

    # ------------------------------------------------------------------ #
    # Sync de grupos                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _sync_groups(user, claims: dict[str, Any]) -> None:
        """
        Espelha no Django a lista de grupos vinda no claim ``groups``.

        - Grupos novos são criados via get_or_create.
        - ``user.groups.set(...)`` substitui toda a lista — grupos locais que
          não estejam no token são removidos do usuário.
        """
        raw_groups = claims.get("groups") or []
        group_objs = []
        for name in raw_groups:
            # Keycloak pode retornar grupos com prefixo "/" (hierarquia).
            cleaned = name.lstrip("/").strip()
            if not cleaned:
                continue
            group, _created = Group.objects.get_or_create(name=cleaned)
            group_objs.append(group)
        user.groups.set(group_objs)
