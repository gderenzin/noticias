#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hifenar_paginas_existentes.py
-----------------------------
Migración PUNTUAL: aplica hifenar.hifenar_texto() a los párrafos del cuerpo
(<div class="noticia-detalle-cuerpo">) de todas las noticias ya publicadas
(site/noticia/*.html). Las páginas nuevas ya lo traen desde build_site.
Idempotente: si el cuerpo ya contiene guiones blandos, se omite.

Uso: python scripts/hifenar_paginas_existentes.py [--dry-run]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hifenar import GUION_BLANDO, hifenar_texto  # noqa: E402

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
RE_CUERPO = re.compile(r'(<div class="noticia-detalle-cuerpo">\n)(.*?)(\n      </div>)', re.DOTALL)
RE_PARRAFO = re.compile(r"(<p>)(.*?)(</p>)", re.DOTALL)


def main() -> None:
    dry = "--dry-run" in sys.argv
    rehacer = "--rehacer" in sys.argv  # quita los guiones blandos existentes y los recalcula
    conteo = {"actualizado": 0, "ya_tenia": 0, "sin_cuerpo": 0}
    for ruta in sorted((SITE_DIR / "noticia").glob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        m = RE_CUERPO.search(texto)
        if not m:
            conteo["sin_cuerpo"] += 1
            continue
        cuerpo = m.group(2)
        if GUION_BLANDO in cuerpo:
            if not rehacer:
                conteo["ya_tenia"] += 1
                continue
            cuerpo = cuerpo.replace(GUION_BLANDO, "")
        nuevo_cuerpo = RE_PARRAFO.sub(lambda p: p.group(1) + hifenar_texto(p.group(2)) + p.group(3), cuerpo)
        if not dry:
            ruta.write_text(texto[: m.start(2)] + nuevo_cuerpo + texto[m.end(2):], encoding="utf-8", newline="")
        conteo["actualizado"] += 1
    print(f"[hifenar] {conteo}" + ("  (--dry-run)" if dry else ""))


if __name__ == "__main__":
    main()
