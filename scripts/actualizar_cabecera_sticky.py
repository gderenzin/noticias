#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
actualizar_cabecera_sticky.py
-------------------------------
Script de migración PUNTUAL (no forma parte del workflow diario): aplica
la nueva cabecera (ver render_cabecera() en build_site.py -- centrada,
logo/título más grandes, "derenzin.com" como ítem de la navegación, sin el
subtítulo de cada página) a TODAS las páginas ya publicadas, y reubica ese
subtítulo (antes "Edición del ...", "Detalle de la noticia", etc., dentro
de la cabecera) al principio del contenido principal de cada página (ver
render_subtitulo_pagina()).

Nunca inventa el texto del subtítulo: lo lee tal cual ya está publicado en
cada página (su propio <p class="subtitulo">...</p> dentro de la cabecera
vieja), no lo recalcula -- así una "Edición del 5 de septiembre de 2026"
ya correcta sigue siendo exactamente esa fecha, no una recalculada a
partir del nombre del archivo (que podría no coincidir, ver el propio
historial de reorganización de archivo por fecha real).

Uso:
    py -3 scripts/actualizar_cabecera_sticky.py --dry-run
    py -3 scripts/actualizar_cabecera_sticky.py
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
SITE_DIR = RAIZ / "site"

RE_SUBTITULO_VIEJO = re.compile(r'<p class="subtitulo">(.*?)</p>', re.DOTALL)
RE_HEADER = re.compile(r'  <header class="cabecera">\n.*?\n  </header>\n', re.DOTALL)
RE_MAIN_CON_H1 = re.compile(r'(  <main class="[^"]*">\n    <h1 class="sr-only">.*?</h1>\n)')
RE_MAIN_SIN_H1 = re.compile(r'(  <main class="contenido pagina-noticia">\n)')


def log(mensaje: str) -> None:
    print(f"[actualizar_cabecera_sticky] {mensaje}", flush=True)


def _prefijo_y_pagina(archivo: Path) -> tuple[str, str]:
    rel = archivo.relative_to(SITE_DIR)
    partes = rel.parts
    if partes == ("index.html",):
        return "", "portada"
    if partes[0] == "archivo":
        return "../", "archivo"
    if partes[0] == "proteccion-datos":
        return "../", "proteccion_datos"
    if partes[0] == "noticia":
        return "../", "noticia"
    raise ValueError(f"No se reconoce el tipo de página para {archivo}")


def procesar_archivo(archivo: Path, dry_run: bool) -> str:
    """Devuelve 'actualizado', 'ya_tenia' (ya usa la cabecera nueva) o
    'omitido' (no se encontró el patrón esperado -- no se toca)."""
    texto = archivo.read_text(encoding="utf-8")

    if "cabecera-sticky.js" in texto and "subtitulo-pagina" in texto:
        return "ya_tenia"

    m_header = RE_HEADER.search(texto)
    m_subtitulo = RE_SUBTITULO_VIEJO.search(texto)
    if not m_header or not m_subtitulo:
        return "omitido"

    subtitulo_texto = html_lib.unescape(re.sub(r"<[^>]*>", "", m_subtitulo.group(1))).strip()
    prefijo, pagina_actual = _prefijo_y_pagina(archivo)

    header_nuevo = bs.render_cabecera(prefijo, pagina_actual)
    subtitulo_html = bs.render_subtitulo_pagina(subtitulo_texto)

    nuevo_texto = texto[: m_header.start()] + header_nuevo + texto[m_header.end() :]

    m_main = RE_MAIN_CON_H1.search(nuevo_texto) or RE_MAIN_SIN_H1.search(nuevo_texto)
    if not m_main:
        return "omitido"
    nuevo_texto = nuevo_texto[: m_main.end()] + subtitulo_html + nuevo_texto[m_main.end() :]

    if not dry_run:
        archivo.write_text(nuevo_texto, encoding="utf-8")
    return "actualizado"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada; solo cuenta qué haría.")
    args = parser.parse_args()

    archivos = sorted(SITE_DIR.rglob("*.html"))
    log(f"{len(archivos)} página(s) HTML encontradas en {SITE_DIR}.")

    conteo = {"actualizado": 0, "ya_tenia": 0, "omitido": 0}
    omitidos: list[str] = []
    for archivo in archivos:
        resultado = procesar_archivo(archivo, args.dry_run)
        conteo[resultado] += 1
        if resultado == "omitido":
            omitidos.append(str(archivo.relative_to(SITE_DIR)))

    log(f"Actualizado: {conteo['actualizado']}  |  Ya tenía la cabecera nueva: {conteo['ya_tenia']}  |  Omitidos: {conteo['omitido']}")
    if omitidos:
        log("Archivos omitidos (revisar a mano):")
        for nombre in omitidos:
            log(f"  {nombre}")

    if args.dry_run:
        log("(--dry-run: no se escribió nada.)")

    log("Listo.")


if __name__ == "__main__":
    main()
