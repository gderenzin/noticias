#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quitar_aviso_transparencia_ia.py
------------------------------------
Script de migración PUNTUAL (no forma parte del workflow diario):
espejo inverso de agregar_aviso_transparencia_ia.py -- saca el aviso
"Resumen generado con IA (Gemini)..." (AVISO_TRANSPARENCIA_IA en
build_site.py) de todas las páginas de detalle YA PUBLICADAS que lo
tengan, a pedido explícito de sacarlo del sitio.

build_site.py ya no genera este aviso para noticias nuevas (se sacó de
render_pagina_noticia); este script es solo para poner al día las
páginas que quedaron con el aviso de antes de ese cambio.

NO toca el aviso de "análisis original" (AVISO_ANALISIS_ORIGINAL) --
es un aviso distinto, sobre las piezas de análisis propio de DERENZIN
S.A.S., no sobre el resumen por IA. Lo detecta por el TEXTO del aviso,
no solo por la clase CSS (las dos usan la misma clase
"aviso-transparencia-ia"), para no borrar el aviso equivocado.

Idempotente: si una página no tiene el aviso de IA, se omite sin tocar
nada.

Uso:
    py -3 scripts/quitar_aviso_transparencia_ia.py --dry-run
    py -3 scripts/quitar_aviso_transparencia_ia.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
NOTICIA_DIR = RAIZ / "site" / "noticia"


def log(mensaje: str) -> None:
    print(f"[quitar_aviso_transparencia_ia] {mensaje}", flush=True)


def procesar_archivo(archivo: Path, patron: re.Pattern, dry_run: bool) -> str:
    """Devuelve 'quitado', 'no_tenia' u 'omitido' (el patrón no matcheó
    ninguna línea reconocible -- se deja para revisar a mano, nunca se
    adivina)."""
    texto = archivo.read_text(encoding="utf-8")

    if "Resumen generado con IA (Gemini)" not in texto:
        return "no_tenia"

    nuevo_texto, n = patron.subn("", texto)
    if n == 0:
        return "omitido"

    if not dry_run:
        archivo.write_text(nuevo_texto, encoding="utf-8")
    return "quitado"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada; solo cuenta qué haría.")
    args = parser.parse_args()

    # Matchea exactamente la línea que agregar_aviso_transparencia_ia.py /
    # render_pagina_noticia insertan -- el <p class="aviso-transparencia-ia">
    # cuyo TEXTO es el aviso de IA (no el de "análisis original", que usa la
    # misma clase pero otro texto).
    patron = re.compile(
        r'[ \t]*<p class="aviso-transparencia-ia">'
        + re.escape(bs.AVISO_TRANSPARENCIA_IA)
        + r"</p>\n"
    )

    archivos = sorted(NOTICIA_DIR.glob("*.html"))
    log(f"{len(archivos)} página(s) de noticia encontradas en {NOTICIA_DIR}.")

    conteo = {"quitado": 0, "no_tenia": 0, "omitido": 0}
    omitidos: list[str] = []
    for archivo in archivos:
        resultado = procesar_archivo(archivo, patron, args.dry_run)
        conteo[resultado] += 1
        if resultado == "omitido":
            omitidos.append(archivo.name)

    log(
        f"Quitado: {conteo['quitado']}  |  No lo tenía: {conteo['no_tenia']}  |  "
        f"Omitidos (revisar a mano): {conteo['omitido']}"
    )
    if omitidos:
        log("Archivos omitidos (el texto no matcheó exacto -- revisar a mano):")
        for nombre in omitidos:
            log(f"  {nombre}")

    if args.dry_run:
        log("(--dry-run: no se escribió nada.)")

    log("Listo.")


if __name__ == "__main__":
    main()
