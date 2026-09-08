#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
actualizar_sidebar_sticky.py
-----------------------------
Script de migración PUNTUAL (no forma parte del workflow diario): actualiza
el sidebar de TODAS las páginas de detalle YA PUBLICADAS (site/noticia/*.html)
a la nueva estructura -- envoltorio <div class="sidebar-noticia-sticky">
adentro de <aside class="sidebar-noticia"> (necesario para que
position: sticky tenga recorrido real, ver style.css) y hasta 7 ítems en
"Últimas noticias" (antes 5) -- ver render_sidebar_noticia() en
build_site.py, que ya genera esta estructura para las noticias nuevas.

No re-deriva título/resumen del artículo (mismo motivo que
agregar_sidebar_noticias.py: evitar reintentar una traducción sin
DEEPL_API_KEY disponible) -- solo reemplaza el bloque
<aside class="sidebar-noticia">...</aside> completo por uno recién
generado con obtener_items_recientes()/render_sidebar_noticia(), usando el
enlace real ya publicado en la propia página para excluirse a sí misma de
"Últimas noticias".

Uso:
    py -3 scripts/actualizar_sidebar_sticky.py --dry-run
    py -3 scripts/actualizar_sidebar_sticky.py
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
NOTICIA_DIR = RAIZ / "site" / "noticia"

RE_FUENTE_ENLACE = re.compile(r'<span class="fuente">Fuente: <a href="([^"]+)"[^>]*>')

# Captura el <aside class="sidebar-noticia">...</aside> completo, con o sin
# el envoltorio nuevo adentro -- da igual, se reemplaza entero.
RE_ASIDE = re.compile(r'    <aside class="sidebar-noticia">\n.*?\n    </aside>\n', re.DOTALL)


def log(mensaje: str) -> None:
    print(f"[actualizar_sidebar_sticky] {mensaje}", flush=True)


def procesar_archivo(archivo: Path, dry_run: bool) -> str:
    """Devuelve 'actualizado', 'ya_tenia' (ya usa el envoltorio sticky) o
    'omitido' (no se encontró un <aside class="sidebar-noticia"> ni un
    enlace de fuente -- no se toca, para no arriesgar romper una página con
    un formato distinto al esperado)."""
    texto = archivo.read_text(encoding="utf-8")

    if "sidebar-noticia-sticky" in texto:
        return "ya_tenia"

    m_aside = RE_ASIDE.search(texto)
    m_enlace = RE_FUENTE_ENLACE.search(texto)
    if not m_aside or not m_enlace:
        return "omitido"

    enlace = html_lib.unescape(m_enlace.group(1))
    items_recientes = bs.obtener_items_recientes(enlace, limite=7)
    aside_nuevo = bs.render_sidebar_noticia(items_recientes)

    nuevo_texto = texto[: m_aside.start()] + aside_nuevo + texto[m_aside.end() :]
    if not dry_run:
        archivo.write_text(nuevo_texto, encoding="utf-8")
    return "actualizado"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada; solo cuenta qué haría.")
    args = parser.parse_args()

    archivos = sorted(NOTICIA_DIR.glob("*.html"))
    log(f"{len(archivos)} página(s) de detalle encontradas en {NOTICIA_DIR}.")

    conteo = {"actualizado": 0, "ya_tenia": 0, "omitido": 0}
    omitidos: list[str] = []
    for archivo in archivos:
        resultado = procesar_archivo(archivo, args.dry_run)
        conteo[resultado] += 1
        if resultado == "omitido":
            omitidos.append(archivo.name)

    log(f"Actualizado: {conteo['actualizado']}  |  Ya tenía sticky: {conteo['ya_tenia']}  |  Omitidos: {conteo['omitido']}")
    if omitidos:
        log("Archivos omitidos (revisar a mano):")
        for nombre in omitidos:
            log(f"  {nombre}")

    if args.dry_run:
        log("(--dry-run: no se escribió nada.)")

    log("Listo.")


if __name__ == "__main__":
    main()
