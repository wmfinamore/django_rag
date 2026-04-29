#!/usr/bin/env python3
"""
download_bootstrap.py — Baixa Bootstrap 5 para os arquivos estáticos do projeto.

Coloca os arquivos em static/css/ e static/js/, sem depender de CDN em runtime.

Uso:
    python docker/download_bootstrap.py
"""

import urllib.request
import hashlib
from pathlib import Path

VERSION = "5.3.3"
BASE_URL = f"https://cdn.jsdelivr.net/npm/bootstrap@{VERSION}/dist"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

FILES = [
    # (url_path, destino_local, sha384 esperado ou None para pular verificação)
    (
        f"{BASE_URL}/css/bootstrap.min.css",
        PROJECT_ROOT / "static/css/bootstrap.min.css",
    ),
    (
        f"{BASE_URL}/css/bootstrap.min.css.map",
        PROJECT_ROOT / "static/css/bootstrap.min.css.map",
    ),
    (
        f"{BASE_URL}/js/bootstrap.bundle.min.js",
        PROJECT_ROOT / "static/js/bootstrap.bundle.min.js",
    ),
    (
        f"{BASE_URL}/js/bootstrap.bundle.min.js.map",
        PROJECT_ROOT / "static/js/bootstrap.bundle.min.js.map",
    ),
]


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  ↓ {url.split('/')[-1]}", end="", flush=True)
    urllib.request.urlretrieve(url, dest)
    size_kb = dest.stat().st_size // 1024
    print(f" → {size_kb} KB ✓")


def main() -> None:
    print(f"Bootstrap {VERSION} → {PROJECT_ROOT}/static/\n")
    for url, dest in FILES:
        if dest.exists():
            print(f"  ✓ {dest.name} já existe, pulando.")
            continue
        download(url, dest)

    print(f"\nPronto! Arquivos em static/css/ e static/js/")
    print("Execute 'python manage.py collectstatic' antes de subir para produção.")


if __name__ == "__main__":
    main()
