#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agregar_sidebar_noticias.py
-----------------------------
Script de migración PUNTUAL (no forma parte del workflow diario): agrega el
nuevo sidebar (categorías + "Últimas noticias") a TODAS las páginas de
detalle de noticia YA PUBLICADAS (site/noticia/*.html), para que el sitio
histórico completo quede con el mismo layout que build_site.py ya genera
para las noticias nuevas (ver render_pagina_noticia() / render_sidebar_
noticia() en build_site.py).

Por qué es un script de cirugía de HTML y no simplemente una llamada a
render_pagina_noticia() por cada página: esa función vuelve a correr
preparar_texto_mostrado()/preparar_resumen_ampliado(), que SIEMPRE tratan
item["titulo"] como el título ORIGINAL sin traducir e intentan traducirlo
de nuevo con DeepL. Si se reconstruyera item["titulo"] leyendo el <h1> ya
publicado (que para la mayoría de las noticias reales YA es la traducción
correcta), esa segunda pasada -- sin DEEPL_API_KEY disponible en esta
corrida -- le pondría una nota falsa de "no se pudo traducir" a títulos que
en realidad SÍ se tradujeron bien la primera vez. Para evitar ese riesgo,
este script NUNCA re-deriva el texto mostrado: solo envuelve el <article>
que ya está publicado (tal cual, carácter por carácter) en el nuevo
<div class="pagina-noticia-layout"> junto al <aside> del sidebar, usando
las mismas funciones (obtener_items_recientes/render_sidebar_noticia) que
usa el flujo normal para construirlo.

No toca el contenido de ninguna noticia (título, resumen, imagen, fecha,
categoría) -- solo la estructura HTML alrededor del <article> ya existente.

Uso:
    py -3 scripts/agregar_sidebar_noticias.py --dry-run
    py -3 scripts/agregar_sidebar_noticias.py
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
ARCHIVO_DIR = RAIZ / "site" / "archivo"

RE_FUENTE_ENLACE = re.compile(r'<span class="fuente">Fuente: <a href="([^"]+)"[^>]*>')

# Captura el <main class="contenido pagina-noticia"> tal cual está HOY (sin
# sidebar): el <article> completo, seguido directo de </main>. Si una
# página ya tiene el sidebar (--dry-run corrido dos veces, o una corrida
# parcial anterior), este patrón no matchea -- se detecta y se omite, para
# que el script sea seguro de correr más de una vez.
RE_MAIN_SIN_SIDEBAR = re.compile(
    r'(  <main class="contenido pagina-noticia">\n)'
    # La indentación del </article> de cierre varía un poco entre páginas
    # publicadas por versiones anteriores de la plantilla (algunas quedaron
    # con más espacios de la cuenta) -- [ \t]* la tolera sin exigir un
    # formato exacto; se preserva tal cual viene, no se reindenta (no
    # importa para el HTML final).
    r'(    <article class="noticia-detalle cat-[a-z_]+">.*?\n[ \t]*</article>\n)'
    r'(  </main>\n)',
    re.DOTALL,
)


def log(mensaje: str) -> None:
    print(f"[agregar_sidebar_noticias] {mensaje}", flush=True)


def procesar_archivo(archivo: Path, dry_run: bool) -> str:
    """Devuelve 'agregado', 'ya_tenia' o 'omitido' (no matcheó el patrón
    esperado -- no se toca, para no arriesgar romper una página con un
    formato distinto al esperado)."""
    texto = archivo.read_text(encoding="utf-8")

    if "pagina-noticia-layout" in texto:
        return "ya_tenia"

    m_main = RE_MAIN_SIN_SIDEBAR.search(texto)
    m_enlace = RE_FUENTE_ENLACE.search(texto)
    if not m_main or not m_enlace:
        return "omitido"

    enlace = html_lib.unescape(m_enlace.group(1))
    items_recientes = bs.obtener_items_recientes(enlace, limite=5)
    sidebar_html = bs.render_sidebar_noticia(items_recientes)

    apertura_main, article_html, cierre_main = m_main.groups()
    reemplazo = (
        apertura_main
        + '    <div class="pagina-noticia-layout">\n'
        + article_html
        + sidebar_html
        + "    </div>\n"
        + cierre_main
    )
    nuevo_texto = texto[: m_main.start()] + reemplazo + texto[m_main.end() :]

    if not dry_run:
        archivo.write_text(nuevo_texto, encoding="utf-8")
    return "agregado"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada; solo cuenta qué haría.")
    args = parser.parse_args()

    archivos = sorted(NOTICIA_DIR.glob("*.html"))
    log(f"{len(archivos)} página(s) de detalle encontradas en {NOTICIA_DIR}.")

    conteo = {"agregado": 0, "ya_tenia": 0, "omitido": 0}
    omitidos: list[str] = []
    for archivo in archivos:
        resultado = procesar_archivo(archivo, args.dry_run)
        conteo[resultado] += 1
        if resultado == "omitido":
            omitidos.append(archivo.name)

    log(f"Agregado: {conteo['agregado']}  |  Ya tenía sidebar: {conteo['ya_tenia']}  |  Omitidos (formato inesperado): {conteo['omitido']}")
    if omitidos:
        log("Archivos omitidos (revisar a mano):")
        for nombre in omitidos:
            log(f"  {nombre}")

    if args.dry_run:
        log("(--dry-run: no se escribió nada.)")
        return

    # Índice de archivo: regenerar con el mismo diseño de tarjetas que ya
    # usa build_site.py normalmente (ver render_archivo_index()).
    dias_existentes = sorted({p.stem for p in ARCHIVO_DIR.glob("*.html") if p.stem != "index"})
    (ARCHIVO_DIR / "index.html").write_text(bs.render_archivo_index(dias_existentes), encoding="utf-8")
    log(f"Escrito {ARCHIVO_DIR / 'index.html'} ({len(dias_existentes)} edición/ediciones listadas, diseño de tarjetas).")

    log("Listo.")


if __name__ == "__main__":
    main()
