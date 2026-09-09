#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eliminar_idioma_nota_duplicado.py
------------------------------------
Script de migración PUNTUAL (no forma parte del workflow diario): elimina
el `<span class="idioma-nota">...</span>` de TODAS las páginas ya
publicadas -- portada, cada día de archivo, y la cabecera de cada página
de detalle -- porque esa plantilla de metadatos (`.noticia-meta`) es
compartida entre los cuadritos (portada/archivo/sidebar) y la cabecera de
la página de detalle, y mostrar ahí el aviso de traducción/IA duplicaba
el aviso de transparencia real (`<p class="aviso-transparencia-ia">`, ya
agregado debajo del cuerpo del artículo -- ver AVISO_TRANSPARENCIA_IA en
build_site.py) además de aparecer donde no corresponde (cuadritos).

El único elemento que sobrevive es `<p class="aviso-transparencia-ia">`,
que NO se toca -- no comparte clase ni forma con `idioma-nota`, así que
un barrido global de `idioma-nota` no le afecta.

Es un barrido sobre TODO site/ (no solo site/noticia/) porque el mismo
span aparecía en site/index.html, cada site/archivo/AAAA-MM-DD.html y
site/proteccion-datos/index.html (dondequiera que build_site.py haya
usado render_tarjeta_html).

Uso:
    py -3 scripts/eliminar_idioma_nota_duplicado.py --dry-run
    py -3 scripts/eliminar_idioma_nota_duplicado.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SITE_DIR = RAIZ / "site"

RE_NOTAS = re.compile(r'(?:<span class="idioma-nota">.*?</span>)+')


def log(mensaje: str) -> None:
    print(f"[eliminar_idioma_nota_duplicado] {mensaje}", flush=True)


def procesar_archivo(archivo: Path, dry_run: bool) -> int:
    """Devuelve cuántas coincidencias se eliminaron en este archivo (0 si
    no tenía ninguna)."""
    texto = archivo.read_text(encoding="utf-8")
    nuevo_texto, cantidad = RE_NOTAS.subn("", texto)
    if cantidad and not dry_run:
        archivo.write_text(nuevo_texto, encoding="utf-8")
    return cantidad


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada; solo cuenta qué haría.")
    args = parser.parse_args()

    archivos = sorted(SITE_DIR.rglob("*.html"))
    log(f"{len(archivos)} página(s) HTML encontradas en {SITE_DIR}.")

    archivos_tocados = 0
    total_spans = 0
    for archivo in archivos:
        cantidad = procesar_archivo(archivo, args.dry_run)
        if cantidad:
            archivos_tocados += 1
            total_spans += cantidad

    log(f"Listo. Páginas con al menos un idioma-nota eliminado: {archivos_tocados}  |  Total de bloques eliminados: {total_spans}")
    if args.dry_run:
        log("(--dry-run: no se escribió nada.)")


if __name__ == "__main__":
    main()
