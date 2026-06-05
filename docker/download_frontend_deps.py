#!/usr/bin/env python3
"""
Baixa dependencias de frontend (htmx, marked) para static/js/.

Uso:
    uv run python docker/download_frontend_deps.py
    # ou
    python docker/download_frontend_deps.py
"""
import pathlib
import urllib.request
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent / "static" / "js"
BASE.mkdir(parents=True, exist_ok=True)

DEPS = [
    ("htmx.min.js",   "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js"),
    ("marked.min.js", "https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"),
]

ok = True
for filename, url in DEPS:
    dest = BASE / filename
    if dest.exists():
        print(f"  OK  {filename} (ja existe, {dest.stat().st_size:,} bytes)")
        continue
    try:
        print(f"  ->  Baixando {filename} ...", end=" ", flush=True)
        urllib.request.urlretrieve(url, dest)
        print(f"OK ({dest.stat().st_size:,} bytes)")
    except Exception as e:
        print(f"FALHOU: {e}")
        ok = False

if not ok:
    print("\nAlgum download falhou. Verifique a conexao e tente novamente.")
    sys.exit(1)
else:
    print("\nDependencias de frontend OK.")
    print(f"Arquivos em: {BASE}")
