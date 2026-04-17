#!/usr/bin/env python3
"""
fix_redirect_uris.py — Corrige o redirect_uri do client django_cli no Keycloak.

Problema: Keycloak rejeita "Invalid parameter: redirect_uri" porque
          o Django envia 127.0.0.1 mas só localhost estava cadastrado.

Solução: adiciona as variantes 127.0.0.1 às redirectUris e webOrigins.

Uso:
    python docker/fix_redirect_uris.py
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

KC_BASE = "http://localhost:8081"
ADMIN_USER = "admin"
ADMIN_PASS = "admin"
REALM = "django-rag"
CLIENT_ID = "django_cli"

NEW_REDIRECT_URIS = [
    "http://localhost:8000/rag/oidc/callback/",
    "http://127.0.0.1:8000/rag/oidc/callback/",
    "http://localhost:8000/*",
    "http://127.0.0.1:8000/*",
]
NEW_WEB_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


def req(method, url, data=None, token=None, form=False):
    headers = {}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body = json.loads(raw)
        except Exception:
            body = raw.decode()
        return e.code, body


def main():
    print("Conectando ao Keycloak em", KC_BASE)

    # Obtém token admin
    status, data = req(
        "POST",
        f"{KC_BASE}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
        },
        form=True,
    )
    if status != 200:
        print(f"✗ Erro ao obter token admin: {data}")
        sys.exit(1)
    token = data["access_token"]
    print("✓ Token admin obtido")

    # Busca o client
    status, clients = req(
        "GET",
        f"{KC_BASE}/admin/realms/{REALM}/clients?clientId={CLIENT_ID}",
        token=token,
    )
    if status != 200 or not clients:
        print(f"✗ Client '{CLIENT_ID}' não encontrado no realm '{REALM}'.")
        print("  Execute keycloak_setup.py primeiro.")
        sys.exit(1)

    client_uuid = clients[0]["id"]
    print(f"✓ Client encontrado: {client_uuid}")
    print(f"  redirectUris atuais: {clients[0].get('redirectUris', [])}")

    # Atualiza preservando todos os outros campos
    payload = dict(clients[0])
    payload["redirectUris"] = NEW_REDIRECT_URIS
    payload["webOrigins"] = NEW_WEB_ORIGINS

    status, data = req(
        "PUT",
        f"{KC_BASE}/admin/realms/{REALM}/clients/{client_uuid}",
        data=payload,
        token=token,
    )
    if status in (200, 204):
        print("✓ redirectUris atualizados com sucesso!")
        print(f"  Novas redirectUris: {NEW_REDIRECT_URIS}")
    else:
        print(f"✗ Erro ao atualizar client: {status} {data}")
        sys.exit(1)

    print("\nTente fazer login novamente em http://127.0.0.1:8000/rag/")


if __name__ == "__main__":
    main()
