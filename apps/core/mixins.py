"""
Mixins reutilizáveis para views e serializers do projeto django_rag.

Conteúdo:
    - OwnerQuerysetMixin    — filtra queryset pelo usuário autenticado (owner)
    - LoginRequiredMixin    — versão DRF-compatível do login_required
    - AuditMixin            — injeta o usuário em created_by/updated_by ao salvar
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.contrib.auth.mixins import LoginRequiredMixin as DjangoLoginRequiredMixin
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DRF — Views
# ---------------------------------------------------------------------------


class OwnerQuerysetMixin:
    """
    Mixin para ViewSets DRF que filtra automaticamente o queryset
    pelo campo ``owner`` do model, restringindo os resultados ao
    usuário autenticado na requisição.

    O nome do campo de relacionamento pode ser sobrescrito via
    ``owner_field`` (padrão: ``"owner"``).

    Uso::

        class UserDocumentViewSet(OwnerQuerysetMixin, ModelViewSet):
            queryset = UserDocument.objects.all()
            serializer_class = UserDocumentSerializer
    """

    owner_field: str = "owner"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()  # type: ignore[misc]
        return qs.filter(**{self.owner_field: self.request.user})  # type: ignore[attr-defined]


class StaffOrOwnerMixin:
    """
    Permite acesso irrestrito para is_staff=True; para demais usuários
    filtra o queryset pelo campo ``owner``.

    Útil em endpoints de administração onde o staff precisa ver todos os
    registros mas o usuário comum só vê os seus.
    """

    owner_field: str = "owner"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()  # type: ignore[misc]
        user = self.request.user  # type: ignore[attr-defined]
        if user.is_staff:
            return qs
        return qs.filter(**{self.owner_field: user})


class ObjectOwnerMixin:
    """
    Garante que apenas o dono do objeto (ou staff) possa modificá-lo.
    Deve ser usado junto a ``OwnerQuerysetMixin`` ou sozinho em views
    que já filtram o queryset.

    Levanta ``PermissionDenied`` (HTTP 403) se o usuário não for dono
    e não for staff.
    """

    owner_field: str = "owner"

    def get_object(self):
        obj = super().get_object()  # type: ignore[misc]
        user = self.request.user  # type: ignore[attr-defined]
        owner = getattr(obj, self.owner_field, None)
        if not user.is_staff and owner != user:
            raise PermissionDenied("Você não tem permissão para acessar este objeto.")
        return obj


# ---------------------------------------------------------------------------
# Django CBVs
# ---------------------------------------------------------------------------


class LoginRequiredMixin(DjangoLoginRequiredMixin):
    """
    Sobrescreve o LoginRequiredMixin padrão para apontar o redirect
    para o endpoint OIDC em vez do /accounts/login/ padrão do Django.

    A URL de login é configurada via ``settings.LOGIN_URL``, que já
    está definida como ``/rag/oidc/authenticate/`` em base.py.
    """


# ---------------------------------------------------------------------------
# Serializers DRF
# ---------------------------------------------------------------------------


class CurrentUserDefaultMixin:
    """
    Mixin para serializers que precisam preencher automaticamente
    o campo ``owner`` (ou similar) com o usuário da requisição.

    Uso no serializer::

        class UserDocumentSerializer(CurrentUserDefaultMixin, ModelSerializer):
            owner = serializers.HiddenField(
                default=serializers.CurrentUserDefault()
            )
    """
