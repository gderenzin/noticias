#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
separar_link_byline_cabecera.py
-------------------------------
Migración PUNTUAL: en la cabecera de todas las páginas ya publicadas, el <a>
que envolvía logo + título + byline pasa a envolver solo logo y título por
separado, y "DERENZIN S.A.S." del byline enlaza a https://derenzin.com/
(pestaña nueva) sin anidar links. Páginas nuevas ya salen así de
build_site.render_cabecera(). Idempotente.

Uso: python scripts/separar_link_byline_cabecera.py [--dry-run]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent.parent / "site"

RE_VIEJA = re.compile(
    r'      <a href="(?P<p>[^"]*)index\.html" class="marca-enlace">\n'
    r'        <img src="(?P<a>[^"]*)assets/logo-derenzin\.png" alt="DERENZIN" class="marca-logo">\n'
    r'        <div class="marca-texto">\n'
    r'          <span class="marca-titulo">Noticias de Ciberseguridad</span>\n'
    r'          <span class="marca-byline">Un proyecto de <strong>DERENZIN S\.A\.S\.</strong></span>\n'
    r'        </div>\n'
    r'      </a>\n'
)

NUEVA = (
    '      <div class="marca-enlace">\n'
    '        <a href="{p}index.html" class="marca-logo-enlace" tabindex="-1" aria-hidden="true">\n'
    '          <img src="{a}assets/logo-derenzin.png" alt="" class="marca-logo">\n'
    '        </a>\n'
    '        <div class="marca-texto">\n'
    '          <a href="{p}index.html" class="marca-titulo-enlace"><span class="marca-titulo">Noticias de Ciberseguridad</span></a>\n'
    '          <span class="marca-byline">Un proyecto de <a href="https://derenzin.com/" target="_blank" rel="noopener noreferrer" class="marca-byline-enlace"><strong>DERENZIN S.A.S.</strong></a></span>\n'
    '        </div>\n'
    '      </div>\n'
)


def main() -> None:
    dry = "--dry-run" in sys.argv
    conteo = {"actualizado": 0, "ya_tenia": 0, "omitido": 0}
    for ruta in sorted(SITE_DIR.rglob("*.html")):
        texto = ruta.read_text(encoding="utf-8")
        if "marca-byline-enlace" in texto:
            conteo["ya_tenia"] += 1
            continue
        nuevo, n = RE_VIEJA.subn(lambda m: NUEVA.format(p=m["p"], a=m["a"]), texto, count=1)
        if not n:
            conteo["omitido"] += 1
            print("omitido:", ruta)
            continue
        if not dry:
            ruta.write_text(nuevo, encoding="utf-8", newline="")
        conteo["actualizado"] += 1
    print(f"[byline] {conteo}" + ("  (--dry-run)" if dry else ""))


if __name__ == "__main__":
    main()
