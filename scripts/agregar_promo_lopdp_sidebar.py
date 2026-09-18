#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agregar_promo_lopdp_sidebar.py
------------------------------
Migración PUNTUAL (no es parte del workflow diario): inserta la cuña de
publicidad propia de lopdp.derenzin.com (build_site.SIDEBAR_PROMO_LOPDP) al
principio del sidebar -- antes de "Categorías" -- en TODAS las páginas ya
publicadas, que llevan el sidebar horneado. Contenido nuevo ya la trae desde
render_sidebar_noticia().

Idempotente: si la página ya tiene class="sidebar-promo", se omite.

Uso:
    py -3 scripts/agregar_promo_lopdp_sidebar.py --dry-run
    py -3 scripts/agregar_promo_lopdp_sidebar.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
ANCLA = '<div class="sidebar-noticia-sticky">\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conteo = {"actualizado": 0, "ya_tenia": 0, "omitido": 0}
    for ruta in sorted(SITE_DIR.rglob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        if 'class="sidebar-promo' in texto:
            conteo["ya_tenia"] += 1
        elif ANCLA not in texto:
            conteo["omitido"] += 1
        else:
            if not args.dry_run:
                ruta.write_text(texto.replace(ANCLA, ANCLA + bs.SIDEBAR_PROMO_LOPDP, 1), encoding="utf-8", newline="")
            conteo["actualizado"] += 1
    print(f"[promo_lopdp] {conteo}" + ("  (--dry-run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
