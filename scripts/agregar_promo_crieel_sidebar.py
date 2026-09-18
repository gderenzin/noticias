#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agregar_promo_crieel_sidebar.py
-------------------------------
Migración PUNTUAL: inserta la cuña de crieel.ec (build_site.SIDEBAR_PROMO_CRIEEL)
justo DEBAJO de la de EuroAmericano y antes de "Categorías" en todas las
páginas ya publicadas. Páginas nuevas ya la traen. Idempotente.

Uso: python scripts/agregar_promo_crieel_sidebar.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402

SITE_DIR = Path(__file__).resolve().parent.parent / "site"


def main() -> None:
    dry = "--dry-run" in sys.argv
    conteo = {"actualizado": 0, "ya_tenia": 0, "omitido": 0}
    for ruta in sorted(SITE_DIR.rglob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        if "https://crieel.ec/" in texto:
            conteo["ya_tenia"] += 1
        elif bs.SIDEBAR_PROMO_EUROAMERICANO not in texto:
            conteo["omitido"] += 1
            print("omitido:", ruta)
        else:
            if not dry:
                nuevo = texto.replace(
                    bs.SIDEBAR_PROMO_EUROAMERICANO, bs.SIDEBAR_PROMO_EUROAMERICANO + bs.SIDEBAR_PROMO_CRIEEL, 1
                )
                ruta.write_text(nuevo, encoding="utf-8", newline="")
            conteo["actualizado"] += 1
    print(f"[promo_crieel] {conteo}" + ("  (--dry-run)" if dry else ""))


if __name__ == "__main__":
    main()
