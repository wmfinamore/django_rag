"""
Sincronização Django → Keycloak via Admin REST API.

Escopo: senhas. Quando a senha de um usuário é alterada no Django
(admin, changepassword, formulários), o novo valor é replicado no
Keycloak via ``PUT /admin/realms/{realm}/users/{id}/reset-password``.

O caminho inverso (Keycloak → Django) não existe para senhas por design
do protocolo: o Keycloak nunca expõe senhas nem hashes. Para manter as
duas bases iguais, toda troca de senha deve ser feita pelo Django (que
empurra para o Keycloak) ou exclusivamente pelo Keycloak (e nesse caso
a senha local do Django diverge — usada apenas no fallback ModelBackend).

Configuração (settings / .env):
    KEYCLOAK_BASE_URL        http://localhost:8081
    KEYCLOAK_REALM           django-rag
    KEYCLOAK_ADMIN_USER      admin
    KEYCLOAK_ADMIN_PASSWORD  admin
    KEYCLOAK_PASSWORD_SYNC   True (desliga o sync se False)

Falhas de sync NÃO impedem a troca de senha no Django — são registradas
em log como warning para não travar o fluxo quando o Keycloak está fora.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10  # segundos por chamada HTTP


# ---------------------------------------------------------------------------
# Helpers HTTP
# ---------------------------------------------------------------------------


def _request(
    method: str,
    url: str,
    data: Any = None,
    token: str | None = None,
    content_type: str = "application/json",
) -> tuple[int, Any]:
    body = None
    headers: dict[str, str] = {}

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if data is not None:
        if content_type == "application/x-www-form-urlencoded":
            body = urllib.parse.urlencode(data).encode()
        else:
            body = json.dumps(data).encode()
        headers["Content-Type"] = content_type

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            decoded = json.loads(raw)
        except Exception:
            decoded = raw.decode(errors="replace")
        return exc.code, decoded


def _get_admin_token() -> str | None:
    """Obtém access token do admin no realm master (client admin-cli)."""
    base = getattr(settings, "KEYCLOAK_BASE_URL", "http://localhost:8081")
    user = getattr(settings, "KEYCLOAK_ADMIN_USER", "admin")
    password = getattr(settings, "KEYCLOAK_ADMIN_PASSWORD", "admin")

    status, data = _request(
        "POST",
        f"{base}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": user,
            "password": password,
        },
        content_type="application/x-www-form-urlencoded",
    )
    if status != 200:
        logger.warning("Keycloak sync: falha ao obter token admin (%s): %s", status, data)
        return None
    return data.get("access_token")


def _find_user_id(token: str, user) -> str | None:
    """
    Resolve o ID do usuário no Keycloak.

    Usa ``user.sub`` quando presente (é o próprio ID); caso contrário
    procura por username exato e, se encontrado, grava o sub no Django.
    """
    if user.sub:
        return user.sub

    base = getattr(settings, "KEYCLOAK_BASE_URL", "http://localhost:8081")
    realm = getattr(settings, "KEYCLOAK_REALM", "django-rag")

    status, data = _request(
        "GET",
        f"{base}/admin/realms/{realm}/users"
        f"?username={urllib.parse.quote(user.username)}&exact=true",
        token=token,
    )
    if status != 200 or not data:
        logger.warning(
            "Keycloak sync: usuário '%s' não encontrado no realm '%s' (%s).",
            user.username,
            realm,
            status,
        )
        return None

    kc_id = data[0].get("id")
    if kc_id:
        # Grava o sub para os próximos syncs e para o lookup OIDC.
        type(user).objects.filter(pk=user.pk).update(sub=kc_id)
    return kc_id


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def sync_password_to_keycloak(user, raw_password: str) -> bool:
    """
    Replica a senha do usuário no Keycloak (não-temporária).

    Retorna True em sucesso; False em qualquer falha (já logada).
    Nunca levanta exceção — a troca de senha no Django não pode falhar
    por indisponibilidade do Keycloak.
    """
    if not getattr(settings, "KEYCLOAK_PASSWORD_SYNC", True):
        return False

    try:
        token = _get_admin_token()
        if not token:
            return False

        kc_id = _find_user_id(token, user)
        if not kc_id:
            return False

        base = getattr(settings, "KEYCLOAK_BASE_URL", "http://localhost:8081")
        realm = getattr(settings, "KEYCLOAK_REALM", "django-rag")

        status, data = _request(
            "PUT",
            f"{base}/admin/realms/{realm}/users/{kc_id}/reset-password",
            data={"type": "password", "value": raw_password, "temporary": False},
            token=token,
        )
        if status in (200, 204):
            logger.info(
                "Keycloak sync: senha de '%s' replicada no realm '%s'.",
                user.username,
                realm,
            )
            return True

        logger.warning(
            "Keycloak sync: falha ao replicar senha de '%s' (%s): %s",
            user.username,
            status,
            data,
        )
        return False

    except Exception:
        logger.warning(
            "Keycloak sync: erro inesperado ao replicar senha de '%s'.",
            user.username,
            exc_info=True,
        )
        return False
