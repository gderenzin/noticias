#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rediseñar_pie_oficiales.py
--------------------------
Migración PUNTUAL: en el pie de todas las páginas ya publicadas, reemplaza el
tagline "Un proyecto de DERENZIN S.A.S. — derenzin.com ↗" por el bloque con
las dos páginas oficiales (build_site.PIE_PAGINAS_OFICIALES). El resto de los
cambios del pie (logo, justificado) son CSS. Idempotente.

Uso: python scripts/rediseñar_pie_oficiales.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
VIEJO = (
    '        <p class="pie-tagline">Un proyecto de DERENZIN S.A.S. — <a href="https://derenzin.com" '
    'target="_blank" rel="noopener noreferrer">derenzin.com ↗</a></p>\n'
)


def main() -> None:
    dry = "--dry-run" in sys.argv
    conteo = {"actualizado": 0, "ya_tenia": 0, "omitido": 0}
    for ruta in sorted(SITE_DIR.rglob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        if "pie-oficiales" in texto:
            conteo["ya_tenia"] += 1
        elif VIEJO not in texto:
            conteo["omitido"] += 1
            print("omitido:", ruta)
        else:
            if not dry:
                ruta.write_text(texto.replace(VIEJO, bs.PIE_PAGINAS_OFICIALES, 1), encoding="utf-8", newline="")
            conteo["actualizado"] += 1
    print(f"[pie_oficiales] {conteo}" + ("  (--dry-run)" if dry else ""))


if __name__ == "__main__":
    main()
