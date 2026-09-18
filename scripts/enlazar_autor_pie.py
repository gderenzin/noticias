#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enlazar_autor_pie.py
--------------------
Migración PUNTUAL: en el copyright del pie de todas las páginas ya publicadas,
"Feddor Derenzin Martínez" pasa a "Ing. Feddor Derenzin Martínez" enlazado a
https://derenzin.com/curriculum.html (pestaña nueva). Páginas nuevas ya salen
así de build_site.render_pie(). Idempotente.

Uso: python scripts/enlazar_autor_pie.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
VIEJO = "DERENZIN S.A.S. — Feddor Derenzin Martínez. Todos"
NUEVO = (
    'DERENZIN S.A.S. — <a href="https://derenzin.com/curriculum.html" target="_blank" '
    'rel="noopener noreferrer">Ing. Feddor Derenzin Martínez</a>. Todos'
)


def main() -> None:
    dry = "--dry-run" in sys.argv
    conteo = {"actualizado": 0, "ya_tenia": 0, "omitido": 0}
    for ruta in sorted(SITE_DIR.rglob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        if "curriculum.html" in texto:
            conteo["ya_tenia"] += 1
        elif VIEJO not in texto:
            conteo["omitido"] += 1
            print("omitido:", ruta)
        else:
            if not dry:
                ruta.write_text(texto.replace(VIEJO, NUEVO, 1), encoding="utf-8", newline="")
            conteo["actualizado"] += 1
    print(f"[pie] {conteo}" + ("  (--dry-run)" if dry else ""))


if __name__ == "__main__":
    main()
