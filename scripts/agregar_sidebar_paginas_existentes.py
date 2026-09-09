#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agregar_sidebar_paginas_existentes.py
-----------------------------------------
Script de migración PUNTUAL (no forma parte del workflow diario): aplica
retroactivamente a las páginas YA PUBLICADAS los dos cambios que
build_site.py ya genera solo para contenido nuevo desde ahora en adelante:

1. El sidebar de Categorías/Últimas noticias en TODAS las páginas del
   sitio -- portada, archivo/index.html, cada site/archivo/AAAA-MM-DD.html
   y proteccion-datos/index.html -- no solo en el detalle de noticia (ver
   _envolver_con_sidebar()/render_pagina_index() etc. en build_site.py).
2. El bloque de categorías del sidebar YA EXISTENTE en cada
   site/noticia/*.html (7 categorías con sus links reales a
   /categoria/<slug>.html, en vez de las 6 de antes apuntando todas a
   /archivo/index.html).

Reusa build_site.render_sidebar_noticia() y build_site._envolver_con_sidebar()
tal cual -- nunca arma HTML nuevo por su cuenta.

Idempotente: si una página ya tiene "con-sidebar-grid" (portada/archivo/
proteccion-datos) o ya apunta a /categoria/ (noticia), se omite.

Uso:
    py -3 scripts/agregar_sidebar_paginas_existentes.py --dry-run
    py -3 scripts/agregar_sidebar_paginas_existentes.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
SITE_DIR = RAIZ / "site"

# (ruta relativa a site/, texto del <main> viejo, texto del <main> nuevo)
PAGINAS_CON_LAYOUT = [
    ("index.html", '<main class="contenido portada">', '<main class="contenido portada con-sidebar-grid">'),
    ("archivo/index.html", '<main class="contenido">', '<main class="contenido con-sidebar-grid">'),
    ("proteccion-datos/index.html", '<main class="contenido portada">', '<main class="contenido portada con-sidebar-grid">'),
]

RE_MAIN_ARCHIVO_DIA = ('<main class="contenido archivo-dia">', '<main class="contenido archivo-dia con-sidebar-grid">')

RE_CONTENIDO = re.compile(r'(<p class="subtitulo-pagina">.*?</p>\n)(.*?)(\n  </main>)', re.DOTALL)
RE_CATEGORIAS_UL = re.compile(r'<ul class="sidebar-categorias">.*?</ul>', re.DOTALL)


def log(mensaje: str) -> None:
    print(f"[agregar_sidebar_paginas_existentes] {mensaje}", flush=True)


def agregar_sidebar_a_pagina(ruta: Path, main_viejo: str, main_nuevo: str, sidebar_html: str, dry_run: bool) -> str:
    """'agregado', 'ya_tenia' u 'omitido' (formato no reconocido)."""
    if not ruta.exists():
        return "omitido"
    texto = ruta.read_text(encoding="utf-8")
    if "con-sidebar-grid" in texto:
        return "ya_tenia"
    if main_viejo not in texto:
        log(f"AVISO: {ruta.relative_to(SITE_DIR)} no tiene el <main> esperado; se omite.")
        return "omitido"

    m = RE_CONTENIDO.search(texto)
    if not m:
        log(f"AVISO: {ruta.relative_to(SITE_DIR)} no tiene el patrón subtítulo/main esperado; se omite.")
        return "omitido"

    contenido_envuelto = bs._envolver_con_sidebar(m.group(2), sidebar_html)
    nuevo_texto = texto[: m.start()] + m.group(1) + contenido_envuelto + m.group(3) + texto[m.end() :]
    nuevo_texto = nuevo_texto.replace(main_viejo, main_nuevo, 1)
    if not dry_run:
        ruta.write_text(nuevo_texto, encoding="utf-8")
    return "agregado"


def arreglar_categorias_noticia(ruta: Path, categorias_ul_nueva: str, dry_run: bool) -> str:
    """'corregido', 'ya_tenia' u 'omitido'."""
    texto = ruta.read_text(encoding="utf-8")
    if "/categoria/" in texto:
        return "ya_tenia"
    if not RE_CATEGORIAS_UL.search(texto):
        log(f"AVISO: {ruta.name} no tiene <ul class=\"sidebar-categorias\">; se omite.")
        return "omitido"
    nuevo_texto = RE_CATEGORIAS_UL.sub(categorias_ul_nueva.replace("\\", "\\\\"), texto, count=1)
    if not dry_run:
        ruta.write_text(nuevo_texto, encoding="utf-8")
    return "corregido"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada; solo cuenta qué haría.")
    args = parser.parse_args()

    sidebar_html = bs.render_sidebar_noticia(bs.obtener_items_recientes(None))
    m_categorias = RE_CATEGORIAS_UL.search(sidebar_html)
    categorias_ul_nueva = m_categorias.group(0)

    conteo_layout = {"agregado": 0, "ya_tenia": 0, "omitido": 0}
    for rel, main_viejo, main_nuevo in PAGINAS_CON_LAYOUT:
        resultado = agregar_sidebar_a_pagina(SITE_DIR / rel, main_viejo, main_nuevo, sidebar_html, args.dry_run)
        conteo_layout[resultado] += 1
        log(f"{rel}: {resultado}")

    for ruta_dia in sorted((SITE_DIR / "archivo").glob("*.html")):
        if ruta_dia.stem == "index":
            continue
        resultado = agregar_sidebar_a_pagina(ruta_dia, *RE_MAIN_ARCHIVO_DIA, sidebar_html, args.dry_run)
        conteo_layout[resultado] += 1

    log(
        f"Layout con sidebar -- agregado: {conteo_layout['agregado']}  |  "
        f"ya tenía: {conteo_layout['ya_tenia']}  |  omitido: {conteo_layout['omitido']}"
    )

    conteo_cat = {"corregido": 0, "ya_tenia": 0, "omitido": 0}
    for ruta_noticia in sorted((SITE_DIR / "noticia").glob("*.html")):
        resultado = arreglar_categorias_noticia(ruta_noticia, categorias_ul_nueva, args.dry_run)
        conteo_cat[resultado] += 1

    log(
        f"Links de categoría en detalle de noticia -- corregido: {conteo_cat['corregido']}  |  "
        f"ya tenía: {conteo_cat['ya_tenia']}  |  omitido: {conteo_cat['omitido']}"
    )

    if args.dry_run:
        log("(--dry-run: no se escribió nada.)")
    log("Listo.")


if __name__ == "__main__":
    main()
