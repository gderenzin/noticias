#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agregar_aviso_transparencia_ia.py
------------------------------------
Script de migración PUNTUAL (no forma parte del workflow diario): aplica
el nuevo aviso de transparencia (ver AVISO_TRANSPARENCIA_IA y
render_pagina_noticia en build_site.py) a las páginas de detalle YA
PUBLICADAS antes de que existiera esta plantilla -- para que las 141
páginas actuales queden consistentes con lo que build_site.py genera
desde ahora en adelante para las noticias nuevas.

Nunca inventa si una noticia pasó o no por Gemini: lo detecta leyendo la
propia página ya publicada (si contiene la nota "Resumen generado con IA
(Gemini)" que resumir_ia.py/build_site.py ya le puso en su momento). Las
noticias de Protección de Datos (SPDP) -- que se copian/parafrasean
directo de los boletines oficiales, sin pasar por Gemini (ver
`omitir_resumen_ia` en fetch_spdp.py) -- nunca tienen esa nota, así que
quedan correctamente excluidas sin necesidad de mirar su categoría.

Idempotente: si una página ya tiene el aviso (por una corrida anterior de
este script, o porque se generó de cero con la plantilla ya actualizada),
se omite.

Uso:
    py -3 scripts/agregar_aviso_transparencia_ia.py --dry-run
    py -3 scripts/agregar_aviso_transparencia_ia.py
"""

from __future__ import annotations

import argparse
import re
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
NOTICIA_DIR = RAIZ / "site" / "noticia"

RE_CIERRE_CUERPO = re.compile(r'(      <div class="noticia-detalle-cuerpo">.*?\n      </div>\n)', re.DOTALL)


def log(mensaje: str) -> None:
    print(f"[agregar_aviso_transparencia_ia] {mensaje}", flush=True)


def procesar_archivo(archivo: Path, dry_run: bool) -> str:
    """Devuelve 'agregado', 'ya_tenia', 'no_aplica' (Plan B / Protección de
    Datos, sin resumen de Gemini) u 'omitido' (formato no reconocido)."""
    texto = archivo.read_text(encoding="utf-8")

    if 'class="aviso-transparencia-ia"' in texto:
        return "ya_tenia"

    if "Resumen generado con IA (Gemini)" not in texto:
        return "no_aplica"

    m_cuerpo = RE_CIERRE_CUERPO.search(texto)
    if not m_cuerpo:
        return "omitido"

    aviso_html = f'      <p class="aviso-transparencia-ia">{escape(bs.AVISO_TRANSPARENCIA_IA)}</p>\n'
    nuevo_texto = texto[: m_cuerpo.end()] + aviso_html + texto[m_cuerpo.end() :]

    if not dry_run:
        archivo.write_text(nuevo_texto, encoding="utf-8")
    return "agregado"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada; solo cuenta qué haría.")
    args = parser.parse_args()

    archivos = sorted(NOTICIA_DIR.glob("*.html"))
    log(f"{len(archivos)} página(s) de noticia encontradas en {NOTICIA_DIR}.")

    conteo = {"agregado": 0, "ya_tenia": 0, "no_aplica": 0, "omitido": 0}
    omitidos: list[str] = []
    for archivo in archivos:
        resultado = procesar_archivo(archivo, args.dry_run)
        conteo[resultado] += 1
        if resultado == "omitido":
            omitidos.append(archivo.name)

    log(
        f"Agregado: {conteo['agregado']}  |  Ya lo tenía: {conteo['ya_tenia']}  |  "
        f"No aplica (sin resumen de Gemini -- Plan B/Protección de Datos): {conteo['no_aplica']}  |  "
        f"Omitidos (formato no reconocido): {conteo['omitido']}"
    )
    if omitidos:
        log("Archivos omitidos (revisar a mano):")
        for nombre in omitidos:
            log(f"  {nombre}")

    if args.dry_run:
        log("(--dry-run: no se escribió nada.)")

    log("Listo.")


if __name__ == "__main__":
    main()
