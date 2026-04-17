#!/usr/bin/env python3
"""
fix_pkce.py — Remove a exigência de PKCE do client django_cli.

Problema: o Keycloak retorna error=invalid_request porque o client
          foi criado com pkce.code.challenge.method=S256, mas o
          mozilla-django-oidc não envia code_challenge no fluxo.

Uso:
    python docker/fix_pkce.py
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

    # Token admin
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
        print(f"✗ Erro ao obter token: {data}")
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
        print(f"✗ Client '{CLIENT_ID}' não encontrado. Rode keycloak_setup.py primeiro.")
        sys.exit(1)

    client_uuid = clients[0]["id"]
    current_pkce = clients[0].get("attributes", {}).get("pkce.code.challenge.method", "(não definido)")
    print(f"✓ Client encontrado: {client_uuid}")
    print(f"  PKCE atual: {current_pkce!r}")

    # Remove PKCE e garante redirectUris corretas
    payload = dict(clients[0])
    payload["attributes"] = dict(payload.get("attributes") or {})
    payload["attributes"]["pkce.code.challenge.method"] = ""
    payload["redirectUris"] = [
        "http://localhost:8000/rag/oidc/callback/",
        "http://127.0.0.1:8000/rag/oidc/callback/",
        "http://localhost:8000/*",
        "http://127.0.0.1:8000/*",
    ]
    payload["webOrigins"] = ["http://localhost:8000", "http://127.0.0.1:8000"]

    status, data = req(
        "PUT",
        f"{KC_BASE}/admin/realms/{REALM}/clients/{client_uuid}",
        data=payload,
        token=token,
    )
    if status in (200, 204):
        print("✓ PKCE removido com sucesso!")
        print("✓ redirectUris atualizadas!")
        print("\nReinicie o Django e tente o login novamente.")
    else:
        print(f"✗ Erro: {status} {data}")
        sys.exit(1)


if __name__ == "__main__":
    main()
