"""
Testes da app accounts.

Cobertura:
    TestCustomUserModel         -- campos, __str__, sub nulo, avatar_url default
    TestCustomUserCreationForm  -- campos obrigatorios presentes
    TestCustomUserChangeForm    -- password e usable_password incluidos (correcao Django 5+)
    TestCustomUserAdmin         -- verificacao de sistema sem erros de campo desconhecido
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import TestCase

from apps.accounts.admin import CustomUserAdmin
from apps.accounts.forms import CustomUserChangeForm, CustomUserCreationForm
from apps.accounts.models import CustomUser


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCustomUserModel:

    def test_str_retorna_username_quando_sem_nome(self):
        user = CustomUser.objects.create_user(username="joao", password="senha123")
        assert str(user) == "joao"

    def test_str_retorna_nome_completo_quando_disponivel(self):
        user = CustomUser.objects.create_user(
            username="joao",
            password="senha123",
            first_name="João",
            last_name="Silva",
        )
        assert str(user) == "João Silva"

    def test_sub_nulo_por_padrao(self):
        user = CustomUser.objects.create_user(username="local", password="senha123")
        assert user.sub is None

    def test_sub_unico(self):
        from django.db import IntegrityError
        CustomUser.objects.create_user(username="u1", password="x", sub="sub-abc")
        with pytest.raises(IntegrityError):
            CustomUser.objects.create_user(username="u2", password="x", sub="sub-abc")

    def test_avatar_url_vazio_por_padrao(self):
        user = CustomUser.objects.create_user(username="sem_avatar", password="senha123")
        assert user.avatar_url == ""

    def test_campos_customizados_salvos(self):
        user = CustomUser.objects.create_user(
            username="oidc_user",
            password="senha123",
            sub="sub-xyz",
            avatar_url="https://cdn.example.com/avatar.png",
        )
        user.refresh_from_db()
        assert user.sub == "sub-xyz"
        assert user.avatar_url == "https://cdn.example.com/avatar.png"


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


class TestCustomUserCreationForm(TestCase):
    """
    Garante que o formulario de criacao herda de AdminUserCreationForm,
    preservando o campo usable_password exigido pelo add_fieldsets do UserAdmin.
    """

    def setUp(self):
        self.form = CustomUserCreationForm()

    def test_form_contem_username_e_email(self):
        assert "username" in self.form.fields
        assert "email" in self.form.fields

    def test_form_inclui_usable_password(self):
        # AdminUserCreationForm declara usable_password como campo de classe;
        # sem esta heranca o add_fieldsets do UserAdmin levanta system check error.
        assert "usable_password" in self.form.fields


class TestCustomUserChangeForm(TestCase):
    """
    Garante que os campos exigidos pelo UserAdmin.fieldsets estao presentes.
    usable_password pertence ao formulario de criacao, nao ao de edicao.
    """

    def setUp(self):
        self.form = CustomUserChangeForm()

    def test_form_inclui_password(self):
        assert "password" in self.form.fields

    def test_form_inclui_campos_customizados(self):
        assert "sub" in self.form.fields
        assert "avatar_url" in self.form.fields

    def test_form_inclui_campos_padrao_do_usuario(self):
        for campo in ("username", "email", "first_name", "last_name",
                      "is_active", "is_staff", "is_superuser"):
            assert campo in self.form.fields, "Campo '%s' ausente" % campo


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


class TestCustomUserAdmin(TestCase):
    """
    Verifica que o CustomUserAdmin nao gera erros de sistema do Django.
    O erro 'Unknown field(s) (usable_password)' ocorria quando o
    Meta.fields do CustomUserChangeForm nao incluia usable_password,
    campo adicionado ao UserAdmin.fieldsets no Django 5+.
    """

    def setUp(self):
        self.admin = CustomUserAdmin(CustomUser, AdminSite())

    def test_admin_nao_tem_erros_de_sistema(self):
        errors = self.admin.check()
        assert errors == [], "Erros no admin: %s" % errors

    def test_fieldsets_contem_campos_oidc(self):
        todos_campos = [
            campo
            for _, opcoes in self.admin.fieldsets
            for campo in opcoes.get("fields", [])
        ]
        assert "sub" in todos_campos
        assert "avatar_url" in todos_campos
