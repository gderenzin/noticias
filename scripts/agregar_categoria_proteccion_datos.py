#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agregar_categoria_proteccion_datos.py
--------------------------------------
Script de migración PUNTUAL (no forma parte del workflow diario): aplica
retroactivamente a TODAS las páginas YA PUBLICADAS el cambio que
build_site.py ya genera solo para contenido nuevo desde ahora en adelante --
agregar "Protección de Datos" como 8va categoría del sidebar (ver
CATEGORIAS_SIDEBAR en build_site.py, a pedido explícito del dueño del sitio
el 2026-09-14; antes se excluía a propósito por considerarse redundante con
la sección propia de navegación, que sigue existiendo tal cual).

El sidebar de Categorías/Últimas noticias vive HORNEADO (no es un include en
tiempo de request) en cada página ya publicada -- portada, archivo/index,
cada archivo/AAAA-MM-DD.html, cada site/categoria/<slug>.html,
proteccion-datos/index.html y cada site/noticia/*.html -- así que agregar
una categoría nueva a la lista no las actualiza solas; hay que reescribir el
bloque `<ul class="sidebar-categorias">...</ul>` de cada una.

Reusa build_site.render_sidebar_noticia() tal cual para armar el bloque
nuevo -- nunca arma HTML de categorías a mano.

Idempotente: si una página ya tiene "/categoria/proteccion_datos.html" en su
sidebar, se omite.

Uso:
    py -3 scripts/agregar_categoria_proteccion_datos.py --dry-run
    py -3 scripts/agregar_categoria_proteccion_datos.py
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

RE_CATEGORIAS_UL = re.compile(r'<ul class="sidebar-categorias">.*?</ul>', re.DOTALL)
MARCADOR_YA_MIGRADO = "/categoria/proteccion_datos.html"


def log(mensaje: str) -> None:
    print(f"[agregar_categoria_proteccion_datos] {mensaje}", flush=True)


def actualizar_categorias_pagina(ruta: Path, categorias_ul_nueva: str, dry_run: bool) -> str:
    """'actualizado', 'ya_tenia' u 'omitido' (sin el bloque esperado)."""
    texto = ruta.read_text(encoding="utf-8")
    if MARCADOR_YA_MIGRADO in texto:
        return "ya_tenia"
    if not RE_CATEGORIAS_UL.search(texto):
        return "omitido"
    # .replace("\\", "\\\\") por si el bloque trae "\" literal (no debería,
    # pero re.sub interpreta backslashes del reemplazo como grupos si no se
    # escapan -- mismo cuidado que ya toma agregar_sidebar_paginas_existentes.py).
    nuevo_texto = RE_CATEGORIAS_UL.sub(categorias_ul_nueva.replace("\\", "\\\\"), texto, count=1)
    if not dry_run:
        ruta.write_text(nuevo_texto, encoding="utf-8")
    return "actualizado"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada; solo cuenta qué haría.")
    args = parser.parse_args()

    sidebar_html = bs.render_sidebar_noticia(bs.obtener_items_recientes(None))
    m_categorias = RE_CATEGORIAS_UL.search(sidebar_html)
    if not m_categorias:
        log("ERROR: render_sidebar_noticia() no produjo un <ul class=\"sidebar-categorias\">; abortando.")
        sys.exit(1)
    categorias_ul_nueva = m_categorias.group(0)

    conteo = {"actualizado": 0, "ya_tenia": 0, "omitido": 0}
    for ruta in sorted(SITE_DIR.rglob("*.html")):
        resultado = actualizar_categorias_pagina(ruta, categorias_ul_nueva, args.dry_run)
        conteo[resultado] += 1

    log(
        f"Sidebar de categorías -- actualizado: {conteo['actualizado']}  |  "
        f"ya tenía: {conteo['ya_tenia']}  |  omitido (sin sidebar): {conteo['omitido']}"
    )
    if args.dry_run:
        log("(--dry-run: no se escribió nada.)")
    log("Listo.")


if __name__ == "__main__":
    main()
