#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
marca_promos_sidebar.py
-----------------------
Migración PUNTUAL: reemplaza en todas las páginas ya publicadas las dos
cuñas del sidebar (versión genérica) por la versión con marca propia (logo +
colores de cada entidad; ver SIDEBAR_PROMO_* en build_site.py). Idempotente:
si la página ya tiene "sidebar-promo--lopdp", se omite.

Uso: python scripts/marca_promos_sidebar.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402

SITE_DIR = Path(__file__).resolve().parent.parent / "site"

LOPDP_V1 = """      <section class="sidebar-promo" aria-label="Publicidad">
        <p class="sidebar-promo-label">Publicidad</p>
        <p class="sidebar-promo-origen">Un proyecto de DERENZIN S.A.S.</p>
        <h2 class="sidebar-promo-titulo">Metodología de cumplimiento LOPDP</h2>
        <p class="sidebar-promo-texto">Cómo preparar a tu organización para cumplir con la Ley Orgánica de Protección de Datos Personales.</p>
        <a class="sidebar-promo-cta" href="https://lopdp.derenzin.com/" target="_blank" rel="noopener noreferrer">Conocer más ↗</a>
      </section>
"""

EURO_V1 = """      <section class="sidebar-promo" aria-label="Publicidad">
        <p class="sidebar-promo-label">Publicidad</p>
        <h2 class="sidebar-promo-titulo">Tecnológico Universitario EuroAmericano</h2>
        <p class="sidebar-promo-texto">Carreras tecnológicas y universitarias presenciales y virtuales.</p>
        <a class="sidebar-promo-cta" href="https://euroamericano.edu.ec/" target="_blank" rel="noopener noreferrer">Conocer más ↗</a>
      </section>
"""


def main() -> None:
    dry = "--dry-run" in sys.argv
    conteo = {"actualizado": 0, "ya_tenia": 0, "omitido": 0}
    for ruta in sorted(SITE_DIR.rglob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        if "sidebar-promo--lopdp" in texto:
            conteo["ya_tenia"] += 1
        elif LOPDP_V1 + EURO_V1 not in texto:
            conteo["omitido"] += 1
            print("omitido:", ruta)
        else:
            if not dry:
                nuevo = texto.replace(
                    LOPDP_V1 + EURO_V1, bs.SIDEBAR_PROMO_LOPDP + bs.SIDEBAR_PROMO_EUROAMERICANO, 1
                )
                ruta.write_text(nuevo, encoding="utf-8", newline="")
            conteo["actualizado"] += 1
    print(f"[marca_promos] {conteo}" + ("  (--dry-run)" if dry else ""))


if __name__ == "__main__":
    main()
