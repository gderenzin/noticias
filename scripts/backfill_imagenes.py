#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_imagenes.py
---------------------
Script de BACKFILL PUNTUAL -- no forma parte del workflow diario (no lo
llama .github/workflows/diario.yml) y se corre a mano, una sola vez, cuando
hace falta.

Recorre únicamente los artículos YA PUBLICADOS cuya página de detalle
(site/noticia/*.html) muestra actualmente el ícono genérico de categoría
(nunca toca los que ya tienen una imagen real). Para cada uno, entra a su
URL original (el enlace real, ya verificado por fetch_news.py y guardado en
la propia página publicada) y busca <meta property="og:image"> -- o
twitter:image como respaldo -- exactamente igual que hace resumir_ia.py
para las noticias nuevas (de hecho reusa sus mismas funciones
descargar_pagina()/buscar_imagen_pagina(), y el mismo descargar_imagen()/
ledger de imágenes de fetch_news.py). Si encuentra una imagen real, la
descarga y reescribe la página de detalle ya publicada para usarla en vez
del ícono. Si no encuentra nada, deja el ícono genérico tal cual --
NUNCA se genera ni se inventa una imagen.

Nota sobre data/publicadas.json: ese archivo es solo el ledger de
deduplicación de URLs (guid, fuente, título, fechas, ruta de la página) --
nunca almacenó qué imagen usa cada noticia (eso vive únicamente en el HTML
publicado, vía el campo imagen_local que build_site.py resuelve al momento
de renderizar). Por eso este script no lo toca: no hay nada que actualizar
ahí, y agregarle un campo que ningún otro script lee no aportaría nada.

Alcance: además de la página de detalle propia de cada noticia
(site/noticia/<archivo>.html), este script también sincroniza las
miniaturas de esa misma noticia en las tarjetas de portada/archivo
(site/index.html, site/archivo/*.html, site/proteccion-datos/index.html) --
la página de detalle y esas tarjetas deben leer siempre el mismo dato
(imagen_local), nunca quedar desincronizadas entre sí. (Antes esta pasada
solo tocaba la página de detalle; se descubrió que eso dejaba las tarjetas
de portada/archivo ya publicadas mostrando el ícono aunque la página de
detalle de esa misma noticia ya tuviera la imagen real -- ver
sincronizar_tarjetas_grid().)

Uso:
    py -3 scripts/backfill_imagenes.py                 # corre en serio: busca íconos genéricos y les intenta encontrar imagen
    py -3 scripts/backfill_imagenes.py --dry-run        # solo reporta qué haría, no descarga ni escribe
    py -3 scripts/backfill_imagenes.py --limite 3       # prueba con los primeros 3 candidatos
    py -3 scripts/backfill_imagenes.py --resync-grid    # no busca imágenes nuevas: solo repareja portada/archivo con la imagen que YA tiene cada página de detalle
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402 - reusa render_imagen_html()/CATEGORIAS, mismo directorio
import fetch_news as fn  # noqa: E402 - reusa descargar_imagen()/ledger, mismo directorio
import resumir_ia as ri  # noqa: E402 - reusa descargar_pagina()/buscar_imagen_pagina(), mismo directorio

RAIZ = Path(__file__).resolve().parent.parent
SITE_DIR = RAIZ / "site"
NOTICIA_DIR = SITE_DIR / "noticia"
ARCHIVO_DIR = SITE_DIR / "archivo"

# El bloque generado por render_imagen_html() para el ícono de respaldo en
# páginas de detalle (destacada=True siempre en render_pagina_noticia) --
# ver build_site.py::render_imagen_html. Se busca por la clase
# "noticia-imagen--generica" para no depender del orden de las otras clases.
RE_FIGURE_GENERICA = re.compile(
    r'      <figure class="noticia-imagen noticia-imagen--generica noticia-imagen--destacada">\n'
    r'.*?\n'
    r'      </figure>\n',
    re.DOTALL,
)
# La imagen REAL ya aplicada en una página de detalle (ver la rama "if
# ruta_imagen:" de render_imagen_html) -- para leer, de una página de
# detalle ya arreglada, qué imagen usa y así poder reparejar sus tarjetas.
RE_IMG_REAL = re.compile(
    r'<figure class="noticia-imagen noticia-imagen--destacada">\s*'
    r'<span class="categoria-badge">[^<]*</span>\s*'
    r'<img src="(/imagenes/[^"]+)"[^>]*>\s*'
    r'<figcaption>Imagen: ([^<]*)</figcaption>',
)
RE_EYEBROW = re.compile(r'<span class="eyebrow-categoria">([^<]*)</span>')
RE_TITULO = re.compile(r'<h1 class="noticia-detalle-titulo">(.*?)</h1>', re.DOTALL)
RE_FUENTE = re.compile(r'<span class="fuente">Fuente: ([^<]*)</span>')
RE_ENLACE = re.compile(r'<div class="noticia-fuente-final">\s*<p>Fuente: <a href="([^"]+)"')

ETIQUETA_A_SLUG = {info["etiqueta"]: slug for slug, info in bs.CATEGORIAS.items()}
ETIQUETA_A_SLUG[bs.GENERICO["etiqueta"]] = "generico"


def log(mensaje: str) -> None:
    print(f"[backfill_imagenes] {mensaje}", flush=True)


def encontrar_candidatos() -> list[Path]:
    """Páginas de detalle ya publicadas que hoy muestran el ícono genérico."""
    candidatos = []
    for archivo in sorted(NOTICIA_DIR.glob("*.html")):
        texto = archivo.read_text(encoding="utf-8")
        if "noticia-imagen--generica" in texto:
            candidatos.append(archivo)
    return candidatos


def _archivos_con_grid() -> list[Path]:
    """Todos los archivos que pueden contener una tarjeta/destacada de
    alguna noticia (portada, cada edición archivada, y la sección de
    Protección de Datos) -- ver _renderizar_grid() en build_site.py, que
    genera el mismo HTML para las tres."""
    archivos = [SITE_DIR / "index.html", SITE_DIR / "proteccion-datos" / "index.html"]
    archivos += sorted(p for p in ARCHIVO_DIR.glob("*.html") if p.stem != "index")
    return [a for a in archivos if a.exists()]


def sincronizar_tarjetas_grid(ruta_noticia: str, item: dict, categoria: str, titulo_mostrar: str) -> int:
    """Reescribe, en todo archivo de portada/archivo/Protección de Datos que
    incluya una tarjeta o destacada de esta noticia (identificada por su
    `ruta_noticia`, único por artículo), el mismo bloque de imagen que ya
    tiene su página de detalle -- ambos deben salir de la MISMA fuente de
    verdad (item['imagen_local']), nunca quedar desincronizados.

    No asume si la ocurrencia es tarjeta o destacada (la clase CSS y el
    tamaño del ícono/imagen difieren entre ambas -- ver render_imagen_html):
    lo detecta por el propio `<a class="...-imagen-enlace">` que envuelve el
    bloque, y arma el reemplazo con ESE mismo dato -- nunca inventa cuál era.

    Devuelve cuántas ocurrencias se actualizaron (puede ser 0 si esta
    noticia no aparece en ninguna tarjeta ya generada, p.ej. si nunca llegó
    a ser la más reciente del día)."""
    patron = re.compile(
        r'(<a class="(?:tarjeta|destacada)-imagen-enlace" href="' + re.escape(ruta_noticia) + r'">\n)'
        r'(.*?)'
        r'(\n[ \t]*</a>)',
        re.DOTALL,
    )

    def _reemplazo(m: re.Match) -> str:
        destacada = "destacada-imagen-enlace" in m.group(1)
        nuevo_interior = bs.render_imagen_html(item, categoria, titulo_mostrar, destacada=destacada)
        return m.group(1) + nuevo_interior + m.group(3)

    actualizados = 0
    for archivo in _archivos_con_grid():
        texto = archivo.read_text(encoding="utf-8")
        if ruta_noticia not in texto:
            continue
        nuevo_texto, cantidad = patron.subn(_reemplazo, texto)
        if cantidad:
            archivo.write_text(nuevo_texto, encoding="utf-8")
            actualizados += cantidad
            log(f"  Tarjeta sincronizada en {archivo.relative_to(RAIZ)} ({cantidad} ocurrencia(s)).")
    return actualizados


def procesar_articulo(archivo: Path, ledger_imagenes: dict, dry_run: bool) -> str:
    """Procesa un único artículo candidato. Devuelve 'con_imagen', 'sin_imagen'
    o 'error' (falla al descargar la página o al parsear campos esperados --
    nunca lanza excepción hacia afuera, para no tumbar el resto del backfill)."""
    texto = archivo.read_text(encoding="utf-8")

    m_enlace = RE_ENLACE.search(texto)
    m_titulo = RE_TITULO.search(texto)
    m_fuente = RE_FUENTE.search(texto)
    m_eyebrow = RE_EYEBROW.search(texto)
    if not (m_enlace and m_titulo and m_fuente and m_eyebrow):
        log(f"  AVISO: no se pudieron extraer todos los campos esperados de {archivo.name}; se omite.")
        return "error"

    enlace = html_lib.unescape(m_enlace.group(1))
    titulo_mostrar = html_lib.unescape(re.sub(r"<[^>]*>", "", m_titulo.group(1))).strip()
    fuente_texto = html_lib.unescape(m_fuente.group(1)).strip()
    etiqueta = html_lib.unescape(m_eyebrow.group(1)).strip()
    categoria = ETIQUETA_A_SLUG.get(etiqueta, "generico")

    # La carpeta de fecha usada para organizar site/imagenes/<fecha>/ -- se
    # reusa la fecha ya presente en el propio nombre de archivo (AAAA-MM-DD-
    # ...), coherente con la edición a la que pertenece esta noticia.
    m_fecha = re.match(r"^(\d{4}-\d{2}-\d{2})-", archivo.name)
    carpeta_fecha = m_fecha.group(1) if m_fecha else "sin-fecha"

    log(f"Procesando: {titulo_mostrar[:70]}... ({enlace})")

    html_pagina = ri.descargar_pagina(enlace)
    if not html_pagina:
        return "sin_imagen"

    url_imagen = ri.buscar_imagen_pagina(html_pagina)
    if not url_imagen:
        log("  Sin og:image/twitter:image en la página; se deja el ícono.")
        return "sin_imagen"

    if dry_run:
        log(f"  [dry-run] Se encontró imagen ({url_imagen}); no se descarga ni se escribe nada.")
        return "con_imagen"

    ruta_local = fn.descargar_imagen(url_imagen, carpeta_fecha, ledger_imagenes)
    if not ruta_local:
        return "sin_imagen"

    item = {"imagen_local": ruta_local, "fuente": fuente_texto}
    nuevo_bloque = bs.render_imagen_html(item, categoria, titulo_mostrar, destacada=True)

    texto_actualizado, cantidad = RE_FIGURE_GENERICA.subn(nuevo_bloque, texto, count=1)
    if cantidad != 1:
        log(f"  AVISO: no se pudo ubicar el bloque de imagen genérica en {archivo.name} para reemplazarlo; se omite la escritura.")
        return "error"

    archivo.write_text(texto_actualizado, encoding="utf-8")
    log(f"  OK: imagen real encontrada y aplicada ({ruta_local}).")

    ruta_noticia = f"/noticia/{archivo.name}"
    sincronizar_tarjetas_grid(ruta_noticia, item, categoria, titulo_mostrar)

    return "con_imagen"


def encontrar_articulos_con_imagen_real() -> list[Path]:
    """Páginas de detalle ya publicadas que YA tienen una imagen real (no
    ícono) -- para --resync-grid, que no busca imágenes nuevas, solo
    repareja portada/archivo con lo que la página de detalle ya tiene."""
    candidatos = []
    for archivo in sorted(NOTICIA_DIR.glob("*.html")):
        texto = archivo.read_text(encoding="utf-8")
        if "noticia-imagen--generica" in texto:
            continue
        if RE_IMG_REAL.search(texto):
            candidatos.append(archivo)
    return candidatos


def resync_grid(archivo: Path) -> int:
    """Lee la imagen/fuente/categoría/título que YA tiene una página de
    detalle (sin descargar ni buscar nada nuevo) y sincroniza sus tarjetas
    de portada/archivo/Protección de Datos para que coincidan. Devuelve
    cuántas ocurrencias se actualizaron."""
    texto = archivo.read_text(encoding="utf-8")
    m_img = RE_IMG_REAL.search(texto)
    m_titulo = RE_TITULO.search(texto)
    m_eyebrow = RE_EYEBROW.search(texto)
    if not (m_img and m_titulo and m_eyebrow):
        log(f"  AVISO: no se pudieron extraer todos los campos esperados de {archivo.name}; se omite.")
        return 0

    imagen_local, fuente_texto = m_img.groups()
    fuente_texto = html_lib.unescape(fuente_texto).strip()
    titulo_mostrar = html_lib.unescape(re.sub(r"<[^>]*>", "", m_titulo.group(1))).strip()
    etiqueta = html_lib.unescape(m_eyebrow.group(1)).strip()
    categoria = ETIQUETA_A_SLUG.get(etiqueta, "generico")

    item = {"imagen_local": imagen_local, "fuente": fuente_texto}
    ruta_noticia = f"/noticia/{archivo.name}"
    return sincronizar_tarjetas_grid(ruta_noticia, item, categoria, titulo_mostrar)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No descarga ni escribe nada; solo reporta qué encontraría.")
    parser.add_argument("--limite", type=int, default=None, help="Procesar como máximo N candidatos (para pruebas).")
    parser.add_argument(
        "--resync-grid",
        action="store_true",
        help="No busca imágenes nuevas: solo repareja las tarjetas de portada/archivo/Protección de Datos con la imagen que YA tiene cada página de detalle (repara desincronizaciones, p.ej. de backfills anteriores que solo tocaron la página de detalle).",
    )
    args = parser.parse_args()

    if args.resync_grid:
        candidatos = encontrar_articulos_con_imagen_real()
        if args.limite is not None:
            candidatos = candidatos[: args.limite]
        log(f"Páginas de detalle con imagen real a revisar: {len(candidatos)}.")
        total_sincronizadas = 0
        for archivo in candidatos:
            if args.dry_run:
                log(f"  [dry-run] Se revisaría: {archivo.name}")
                continue
            total_sincronizadas += resync_grid(archivo)
        log("")
        log("===== Resumen de --resync-grid =====")
        log(f"Páginas de detalle revisadas: {len(candidatos)}")
        log(f"Ocurrencias de tarjeta/destacada sincronizadas: {total_sincronizadas}")
        if args.dry_run:
            log("(--dry-run: no se escribió nada realmente)")
        return

    candidatos = encontrar_candidatos()
    if args.limite is not None:
        candidatos = candidatos[: args.limite]

    log(f"Candidatos con ícono genérico: {len(candidatos)}.")
    if not candidatos:
        log("Nada que hacer.")
        return

    ledger_imagenes = fn.cargar_ledger_imagenes()
    ledger_cambio = False

    revisados = 0
    con_imagen = 0
    sin_imagen = 0
    errores = 0

    for archivo in candidatos:
        revisados += 1
        resultado = procesar_articulo(archivo, ledger_imagenes, args.dry_run)
        if resultado == "con_imagen":
            con_imagen += 1
            if not args.dry_run:
                ledger_cambio = True
        elif resultado == "sin_imagen":
            sin_imagen += 1
        else:
            errores += 1

    if ledger_cambio:
        fn.guardar_ledger_imagenes(ledger_imagenes)

    log("")
    log("===== Resumen del backfill =====")
    log(f"Artículos revisados: {revisados}")
    log(f"Con imagen real encontrada y aplicada: {con_imagen}")
    log(f"Sin imagen disponible (se dejó el ícono): {sin_imagen}")
    log(f"Errores (no se pudo procesar): {errores}")
    if args.dry_run:
        log("(--dry-run: no se descargó ni se escribió nada realmente)")


if __name__ == "__main__":
    main()
