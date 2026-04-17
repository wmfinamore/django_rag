#!/usr/bin/env python3
"""
keycloak_setup.py — Configuração automática do Keycloak para o django_rag.

Cria via Admin REST API:
  - Realm  : django-rag
  - Client : django_cli  (confidential, authorization code + PKCE)
  - Mapper : groups claim (lista de grupos no JWT/UserInfo)
  - Mapper : preferred_username
  - Grupos : admin, editor, viewer
  - Usuário: testuser  (membro de admin + viewer)

Uso:
    python docker/keycloak_setup.py

Pré-requisito: Keycloak rodando em http://localhost:8081
    docker compose -f docker-compose-infra.yml up -d keycloak

Variáveis de ambiente (opcionais — possuem defaults):
    KC_BASE_URL            http://localhost:8081
    KC_ADMIN_USER          admin
    KC_ADMIN_PASSWORD      admin
    KC_CLIENT_SECRET       (gerado aleatoriamente se omitido)
    KC_TEST_USER_PASSWORD  Test@1234
"""

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

KC_BASE = os.getenv("KC_BASE_URL", "http://localhost:8081")
ADMIN_USER = os.getenv("KC_ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("KC_ADMIN_PASSWORD", "admin")
REALM = "django-rag"
CLIENT_ID = "django_cli"
CLIENT_SECRET = os.getenv("KC_CLIENT_SECRET") or secrets.token_urlsafe(24)
REDIRECT_URI = "http://localhost:8000/rag/oidc/callback/"
REDIRECT_URI_127 = "http://127.0.0.1:8000/rag/oidc/callback/"
TEST_USER = "testuser"
TEST_PASS = os.getenv("KC_TEST_USER_PASSWORD", "Test@1234")
GROUPS = ["admin", "editor", "viewer"]


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
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body_decoded = json.loads(raw)
        except Exception:
            body_decoded = raw.decode(errors="replace")
        return exc.code, body_decoded


def get_admin_token() -> str:
    url = f"{KC_BASE}/realms/master/protocol/openid-connect/token"
    status, data = _request(
        "POST",
        url,
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
        },
        content_type="application/x-www-form-urlencoded",
    )
    if status != 200:
        print(f"  ✗ Falha ao obter token admin: {data}")
        sys.exit(1)
    return data["access_token"]


def wait_for_keycloak(max_seconds: int = 120) -> None:
    print(f"  Aguardando Keycloak em {KC_BASE}...")
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{KC_BASE}/realms/master", timeout=3)
            print("  ✓ Keycloak disponível.")
            return
        except Exception:
            time.sleep(3)
    print("  ✗ Keycloak não ficou disponível a tempo. Verifique o docker.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Operações Keycloak
# ---------------------------------------------------------------------------

def ensure_realm(token: str) -> None:
    print(f"\n[1] Realm '{REALM}'")
    status, _ = _request("GET", f"{KC_BASE}/admin/realms/{REALM}", token=token)
    if status == 200:
        print("  → já existe, pulando.")
        return

    payload = {
        "realm": REALM,
        "displayName": "Django RAG",
        "enabled": True,
        "loginWithEmailAllowed": True,
        "resetPasswordAllowed": True,
        "rememberMe": True,
        "accessTokenLifespan": 300,
        "ssoSessionIdleTimeout": 1800,
    }
    status, data = _request("POST", f"{KC_BASE}/admin/realms", data=payload, token=token)
    if status == 201:
        print(f"  ✓ Realm '{REALM}' criado.")
    else:
        print(f"  ✗ Erro ao criar realm: {data}")
        sys.exit(1)


def ensure_client(token: str) -> str:
    """Cria/atualiza o client e retorna o UUID interno."""
    print(f"\n[2] Client '{CLIENT_ID}'")
    status, clients = _request(
        "GET",
        f"{KC_BASE}/admin/realms/{REALM}/clients?clientId={CLIENT_ID}",
        token=token,
    )

    payload = {
        "clientId": CLIENT_ID,
        "name": "Django RAG",
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,             # confidential
        "secret": CLIENT_SECRET,
        "redirectUris": [
            REDIRECT_URI,
            REDIRECT_URI_127,
            "http://localhost:8000/*",
            "http://127.0.0.1:8000/*",
        ],
        "webOrigins": ["http://localhost:8000", "http://127.0.0.1:8000"],
        "standardFlowEnabled": True,       # Authorization Code
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "authorizationServicesEnabled": False,
        "attributes": {
            # PKCE desabilitado: mozilla-django-oidc não suporta PKCE nativamente.
            # Para habilitar PKCE seria necessário usar outra biblioteca (ex: authlib).
            "pkce.code.challenge.method": "",
        },
    }

    if status == 200 and clients:
        client_uuid = clients[0]["id"]
        status, data = _request(
            "PUT",
            f"{KC_BASE}/admin/realms/{REALM}/clients/{client_uuid}",
            data=payload,
            token=token,
        )
        if status in (200, 204):
            print(f"  ✓ Client atualizado (uuid={client_uuid}).")
        else:
            print(f"  ✗ Erro ao atualizar client: {data}")
            sys.exit(1)
        return client_uuid

    status, data = _request(
        "POST",
        f"{KC_BASE}/admin/realms/{REALM}/clients",
        data=payload,
        token=token,
    )
    if status == 201:
        # Location: .../clients/<uuid>
        _, clients = _request(
            "GET",
            f"{KC_BASE}/admin/realms/{REALM}/clients?clientId={CLIENT_ID}",
            token=token,
        )
        client_uuid = clients[0]["id"]
        print(f"  ✓ Client criado (uuid={client_uuid}).")
        return client_uuid
    else:
        print(f"  ✗ Erro ao criar client: {data}")
        sys.exit(1)


def ensure_groups_mapper(token: str, client_uuid: str) -> None:
    """Adiciona mapper que injeta o claim 'groups' no token."""
    print("\n[3] Protocol mapper — groups claim")
    url = f"{KC_BASE}/admin/realms/{REALM}/clients/{client_uuid}/protocol-mappers/models"
    _, mappers = _request("GET", url, token=token)

    existing_names = {m.get("name") for m in (mappers if isinstance(mappers, list) else [])}

    mappers_to_create = [
        {
            "name": "groups",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-group-membership-mapper",
            "config": {
                "full.path": "false",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
                "claim.name": "groups",
            },
        },
        {
            "name": "given name",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {
                "user.attribute": "firstName",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
                "claim.name": "given_name",
                "jsonType.label": "String",
            },
        },
        {
            "name": "family name",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-usermodel-attribute-mapper",
            "config": {
                "user.attribute": "lastName",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "userinfo.token.claim": "true",
                "claim.name": "family_name",
                "jsonType.label": "String",
            },
        },
    ]

    for mapper in mappers_to_create:
        if mapper["name"] in existing_names:
            print(f"  → mapper '{mapper['name']}' já existe, pulando.")
            continue
        status, data = _request("POST", url, data=mapper, token=token)
        if status == 201:
            print(f"  ✓ Mapper '{mapper['name']}' criado.")
        else:
            print(f"  ✗ Erro ao criar mapper '{mapper['name']}': {data}")


def ensure_groups(token: str) -> dict[str, str]:
    """Cria os grupos e retorna {nome: uuid}."""
    print("\n[4] Grupos")
    _, existing = _request("GET", f"{KC_BASE}/admin/realms/{REALM}/groups", token=token)
    existing_map = {g["name"]: g["id"] for g in (existing if isinstance(existing, list) else [])}

    result: dict[str, str] = {}
    for name in GROUPS:
        if name in existing_map:
            print(f"  → grupo '{name}' já existe, pulando.")
            result[name] = existing_map[name]
            continue
        status, data = _request(
            "POST",
            f"{KC_BASE}/admin/realms/{REALM}/groups",
            data={"name": name},
            token=token,
        )
        if status == 201:
            # Busca uuid após criação
            _, groups = _request(
                "GET",
                f"{KC_BASE}/admin/realms/{REALM}/groups?search={name}",
                token=token,
            )
            gid = next((g["id"] for g in groups if g["name"] == name), None)
            result[name] = gid
            print(f"  ✓ Grupo '{name}' criado (uuid={gid}).")
        else:
            print(f"  ✗ Erro ao criar grupo '{name}': {data}")

    return result


def ensure_test_user(token: str, group_ids: dict[str, str]) -> None:
    """Cria usuário de teste e o adiciona nos grupos admin + viewer."""
    print(f"\n[5] Usuário de teste '{TEST_USER}'")
    _, users = _request(
        "GET",
        f"{KC_BASE}/admin/realms/{REALM}/users?username={TEST_USER}",
        token=token,
    )

    if users:
        user_id = users[0]["id"]
        print(f"  → usuário já existe (uuid={user_id}), atualizando senha.")
    else:
        payload = {
            "username": TEST_USER,
            "email": f"{TEST_USER}@example.com",
            "firstName": "Test",
            "lastName": "User",
            "enabled": True,
            "emailVerified": True,
            "credentials": [
                {"type": "password", "value": TEST_PASS, "temporary": False}
            ],
        }
        status, data = _request(
            "POST",
            f"{KC_BASE}/admin/realms/{REALM}/users",
            data=payload,
            token=token,
        )
        if status != 201:
            print(f"  ✗ Erro ao criar usuário: {data}")
            sys.exit(1)

        _, users = _request(
            "GET",
            f"{KC_BASE}/admin/realms/{REALM}/users?username={TEST_USER}",
            token=token,
        )
        user_id = users[0]["id"]
        print(f"  ✓ Usuário criado (uuid={user_id}).")

    # Reset de senha (caso já existisse com senha diferente)
    _request(
        "PUT",
        f"{KC_BASE}/admin/realms/{REALM}/users/{user_id}/reset-password",
        data={"type": "password", "value": TEST_PASS, "temporary": False},
        token=token,
    )

    # Associa grupos
    for group_name in ["admin", "viewer"]:
        gid = group_ids.get(group_name)
        if not gid:
            continue
        status, _ = _request(
            "PUT",
            f"{KC_BASE}/admin/realms/{REALM}/users/{user_id}/groups/{gid}",
            token=token,
        )
        if status in (200, 204):
            print(f"  ✓ Usuário adicionado ao grupo '{group_name}'.")
        else:
            print(f"  ✗ Erro ao adicionar ao grupo '{group_name}'.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("  Keycloak Setup — django_rag")
    print("=" * 60)

    wait_for_keycloak()

    token = get_admin_token()
    print("  ✓ Token admin obtido.")

    ensure_realm(token)
    # Renova token após criação do realm
    token = get_admin_token()

    client_uuid = ensure_client(token)
    ensure_groups_mapper(token, client_uuid)
    group_ids = ensure_groups(token)
    ensure_test_user(token, group_ids)

    print("\n" + "=" * 60)
    print("  Configuração concluída!")
    print("=" * 60)
    print(f"""
  Realm          : {REALM}
  Client ID      : {CLIENT_ID}
  Client Secret  : {CLIENT_SECRET}
  Redirect URI   : {REDIRECT_URI}

  Usuário de teste:
    username : {TEST_USER}
    password : {TEST_PASS}
    grupos   : admin, viewer

  Endpoints OIDC:
    Auth     : {KC_BASE}/realms/{REALM}/protocol/openid-connect/auth
    Token    : {KC_BASE}/realms/{REALM}/protocol/openid-connect/token
    UserInfo : {KC_BASE}/realms/{REALM}/protocol/openid-connect/userinfo
    JWKS     : {KC_BASE}/realms/{REALM}/protocol/openid-connect/certs

  Atualize o .env com:
    OIDC_RP_CLIENT_ID={CLIENT_ID}
    OIDC_RP_CLIENT_SECRET={CLIENT_SECRET}
""")


if __name__ == "__main__":
    main()
