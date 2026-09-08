#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reorganizar_archivo_por_fecha_real.py
--------------------------------------
Script de reorganización PUNTUAL (no forma parte del workflow diario):
corrige site/archivo/AAAA-MM-DD.html ya publicados para que cada noticia
quede archivada bajo la fecha calendario (Guayaquil) de su publicación
REAL (item["fecha_publicacion_iso"]), no bajo la fecha en que corrió el
workflow que la publicó.

Este desajuste existía porque build_site.py, antes de corregirse (ver
main() y fecha_publicacion_gye()), agrupaba TODAS las noticias de una
corrida bajo la fecha de esa corrida, sin mirar la fecha de publicación de
cada ítem por separado. En la corrida real del 6 de septiembre de 2026, 8
artículos con fecha de publicación real del 5 de septiembre (llegaron
tarde a los feeds) quedaron archivados bajo site/archivo/2026-09-06.html
en vez de su propio site/archivo/2026-09-05.html. Este script corrige ese
histórico ya publicado; el fix en build_site.py evita que vuelva a pasar
en corridas futuras.

Qué hace:
  1. Lee data/publicadas.json (el ledger real) para saber la fecha de
     publicación real de cada noticia ya publicada.
  2. Escanea site/archivo/*.html (excepto index.html) y detecta cualquier
     noticia cuya fecha real no coincida con el archivo donde está
     archivada -- no depende de una lista fija de artículos, así que sirve
     para cualquier desajuste real que haya, no solo el del 5/6 de
     septiembre.
  3. Para cada archivo de origen afectado, reconstruye TODOS sus ítems
     (leyendo cada página de detalle ya publicada -- título, fuente,
     categoría, imagen, fecha, y el resumen de IA si ya lo tiene, mismo
     mecanismo que reprocesar_ia_septiembre.py) y lo regenera solo con los
     que de verdad pertenecen a esa fecha.
  4. Para cada fecha de destino, junta los ítems que le corresponden (los
     que se movieron, más los que ya estuvieran ahí si el archivo de
     destino ya existía) y (re)genera esa página completa, usando las
     mismas funciones de build_site.py que usa el flujo normal
     (_renderizar_grid / render_pagina_archivo_dia), para garantizar salida
     idéntica al pipeline real.
  5. Regenera site/archivo/index.html y site/sitemap.xml para que reflejen
     los archivos nuevos/actualizados.

Nunca inventa contenido: cada ítem movido conserva exactamente su
categoría, imagen y resumen (de IA si ya lo tenía, o el extracto/Plan B
reconstruido de su página ya publicada si no) -- solo cambia bajo qué
archivo/AAAA-MM-DD.html aparece.

No toca site/index.html (la portada, que ya refleja la edición real más
reciente y no se ve afectada por una reorganización de días anteriores) ni
las páginas de detalle de cada noticia (site/noticia/*.html -- sus URLs no
cambian, para no romper enlaces ya publicados/indexados) ni
data/publicadas.json (las rutas de cada noticia no cambian).

Uso:
    py -3 scripts/reorganizar_archivo_por_fecha_real.py --dry-run
    py -3 scripts/reorganizar_archivo_por_fecha_real.py
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVO_DIR = RAIZ / "site" / "archivo"
NOTICIA_DIR = RAIZ / "site" / "noticia"
PUBLICADAS_JSON = RAIZ / "data" / "publicadas.json"
ZONA_GUAYAQUIL = timezone(timedelta(hours=-5))

RE_TITULO = re.compile(r'<h1 class="noticia-detalle-titulo">(.*?)</h1>', re.DOTALL)
RE_EYEBROW = re.compile(r'<span class="eyebrow-categoria">([^<]*)</span>')
RE_FUENTE_ENLACE = re.compile(r'<span class="fuente">Fuente: <a href="([^"]+)"[^>]*>([^<]*)</a></span>')
RE_FECHA_JSONLD = re.compile(r'"datePublished":\s*"([^"]+)"')
RE_IMG_REAL = re.compile(
    r'noticia-imagen--destacada">\s*<span class="categoria-badge">[^<]*</span>\s*<img src="(/imagenes/[^"]+)"'
)
RE_CUERPO = re.compile(r'noticia-detalle-cuerpo">\n(.*?)\n\s*</div>', re.DOTALL)
RE_P = re.compile(r"<p>(.*?)</p>")
RE_HREF_NOTICIA = re.compile(r'href="(/noticia/[^"]+)"')
# Categoría de cada tarjeta/destacada TAL COMO está archivada hoy -- la clase
# cat-<categoria> del propio <article> es la fuente de verdad ya establecida
# por el fix de categoria-badge/eyebrow-categoria (ver build_site.py::
# sincronizar_badges_categoria()): sale del mismo `categoria` que el resto de
# la tarjeta en el momento del build y nunca se toca por separado. Este
# script SIEMPRE usa esta categoría (la del archivo de origen), nunca la que
# diga la página de detalle -- si algún día difieren, es un desajuste aparte
# que este script no intenta resolver, solo evita empeorarlo.
RE_ARTICLE_CAT_HREF = re.compile(
    r'<article class="(?:destacada|tarjeta) cat-([a-z_]+)">.*?href="(/noticia/[^"]+)"', re.DOTALL
)

ETIQUETA_A_SLUG = {info["etiqueta"]: slug for slug, info in bs.CATEGORIAS.items()}
ETIQUETA_A_SLUG[bs.GENERICO["etiqueta"]] = "generico"

NOTA_GEMINI_MARCA = "Resumen generado con IA (Gemini)"


def log(mensaje: str) -> None:
    print(f"[reorganizar_archivo_por_fecha_real] {mensaje}", flush=True)


def _texto_plano(html_fragmento: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]*>", "", html_fragmento)).strip()


def cargar_ledger() -> dict[str, dict]:
    """ruta_noticia -> {"fecha_publicacion_iso": ...} a partir de
    data/publicadas.json (indexado ahí por enlace externo, no por ruta)."""
    if not PUBLICADAS_JSON.exists():
        log(f"ERROR: no existe {PUBLICADAS_JSON}; no se puede auditar sin el ledger real.")
        sys.exit(1)
    datos = json.loads(PUBLICADAS_JSON.read_text(encoding="utf-8"))
    por_ruta: dict[str, dict] = {}
    for info in datos.get("urls", {}).values():
        ruta = info.get("ruta_noticia")
        if ruta:
            por_ruta[ruta] = info
    return por_ruta


def fecha_gye_de_iso(fecha_iso: str) -> str:
    fecha_utc = datetime.fromisoformat(fecha_iso)
    if fecha_utc.tzinfo is None:
        fecha_utc = fecha_utc.replace(tzinfo=timezone.utc)
    return fecha_utc.astimezone(ZONA_GUAYAQUIL).strftime("%Y-%m-%d")


def mapa_categoria_actual() -> dict[str, str]:
    """{ruta_noticia: categoria} para cada noticia archivada hoy, leído de la
    clase cat-<categoria> de su propio <article> en site/archivo/*.html (ver
    RE_ARTICLE_CAT_HREF) -- la misma fuente de verdad que usa
    sincronizar_badges_categoria() en build_site.py."""
    mapa: dict[str, str] = {}
    for p in sorted(ARCHIVO_DIR.glob("*.html")):
        if p.stem == "index":
            continue
        contenido = p.read_text(encoding="utf-8")
        for categoria, ruta in RE_ARTICLE_CAT_HREF.findall(contenido):
            mapa[ruta] = categoria
    return mapa


def leer_item_publicado(ruta_noticia: str, categoria: str | None = None) -> dict | None:
    """Reconstruye los campos de un ítem a partir de su página de detalle YA
    publicada -- mismo mecanismo que reprocesar_ia_septiembre.py. Si la
    página ya muestra un resumen de IA (Gemini), se preserva tal cual
    (resumen_ia_ok=True); si no, se reconstruye el Plan B actual como
    respaldo real -- en ningún caso se inventa ni se vuelve a llamar a
    ningún servicio externo (este script solo reordena, no reprocesa).

    `categoria`, si se pasa, reemplaza la que se leería del eyebrow-categoria
    de la página de detalle -- usar siempre la categoría tal como está
    archivada HOY (ver mapa_categoria_actual()), no la de la página de
    detalle: son dos representaciones que en teoría deberían coincidir, pero
    si alguna vez difieren, este script no debe introducir un TERCER valor
    ni cambiar lo que el lector ya está viendo en el archivo."""
    archivo = NOTICIA_DIR / Path(ruta_noticia).name
    if not archivo.exists():
        log(f"  AVISO: no existe la página de detalle {archivo} referenciada en el ledger; se omite.")
        return None
    texto = archivo.read_text(encoding="utf-8")

    m_titulo = RE_TITULO.search(texto)
    m_eyebrow = RE_EYEBROW.search(texto)
    m_fuente_enlace = RE_FUENTE_ENLACE.search(texto)
    m_fecha = RE_FECHA_JSONLD.search(texto)
    m_cuerpo = RE_CUERPO.search(texto)
    if not (m_titulo and m_eyebrow and m_fuente_enlace and m_fecha and m_cuerpo):
        log(f"  AVISO: no se pudieron extraer todos los campos esperados de {archivo.name}; se omite.")
        return None

    titulo = _texto_plano(m_titulo.group(1))
    if categoria is None:
        etiqueta = _texto_plano(m_eyebrow.group(1))
        categoria = ETIQUETA_A_SLUG.get(etiqueta, "generico")
    enlace = html_lib.unescape(m_fuente_enlace.group(1))
    fuente_nombre = _texto_plano(m_fuente_enlace.group(2))
    fecha_iso = m_fecha.group(1)

    m_img = RE_IMG_REAL.search(texto)
    imagen_local = m_img.group(1) if m_img else None

    parrafos_actuales = [_texto_plano(p) for p in RE_P.findall(m_cuerpo.group(1)) if p.strip()]
    contenido_actual = "\n\n".join(parrafos_actuales)

    item: dict = {
        "archivo": archivo,
        "titulo": titulo,
        "fuente": fuente_nombre,
        "enlace": enlace,
        "guid": enlace,
        "fecha_publicacion_iso": fecha_iso,
        "categoria": categoria,
        "imagen_local": imagen_local,
        "extracto_original": contenido_actual,
        "contenido_ampliado": contenido_actual,
    }
    if NOTA_GEMINI_MARCA in texto:
        item["resumen_ia"] = contenido_actual
        item["resumen_ia_ok"] = True
    else:
        item["resumen_ia"] = None
        item["resumen_ia_ok"] = False
    return item


def auditar() -> dict[str, list[str]]:
    """Devuelve {ruta_noticia: fecha_real} para todo ítem archivado bajo un
    día distinto al de su fecha_publicacion_iso real."""
    ledger = cargar_ledger()
    desajustes: dict[str, str] = {}
    for p in sorted(ARCHIVO_DIR.glob("*.html")):
        if p.stem == "index":
            continue
        fecha_archivo = p.stem
        contenido = p.read_text(encoding="utf-8")
        rutas = sorted(set(RE_HREF_NOTICIA.findall(contenido)))
        for ruta in rutas:
            info = ledger.get(ruta)
            if not info:
                log(f"  AVISO: {ruta} (en {p.name}) no tiene entrada en el ledger; se omite del audit.")
                continue
            fecha_real = fecha_gye_de_iso(info["fecha_publicacion_iso"])
            if fecha_real != fecha_archivo:
                desajustes[ruta] = fecha_real
    return desajustes


def regenerar_dia(fecha: str, items: list[dict]) -> None:
    """Regenera site/archivo/{fecha}.html entero a partir de la lista de
    ítems que de verdad le corresponden a esa fecha (ya reconstruidos desde
    sus páginas de detalle)."""
    if not items:
        # No debería pasar en este script (solo se llama con fechas que
        # tienen al menos un ítem), pero por si acaso no se borra un
        # archivo existente sin ítems que lo reemplacen.
        log(f"  AVISO: {fecha} quedó sin ítems; no se toca site/archivo/{fecha}.html.")
        return

    items_ordenados = sorted(items, key=lambda x: x["fecha_publicacion_iso"], reverse=True)
    rutas_noticia = {item["enlace"]: f"/noticia/{item['archivo'].name}" for item in items_ordenados}

    _orig_categorizar = bs.categorizar

    def _categorizar_preservada(it):
        return it["categoria"]

    bs.categorizar = _categorizar_preservada
    try:
        items_html = bs._renderizar_grid(items_ordenados, rutas_noticia)
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").replace(hour=12, tzinfo=ZONA_GUAYAQUIL)
        subtitulo = f"Edición del {bs.fecha_legible(fecha_dt)}"
        descripcion_edicion = (
            f"Titulares de ciberseguridad del {bs.fecha_legible(fecha_dt)}, "
            "agregados de fuentes públicas verificadas (The Hacker News, BleepingComputer, Krebs on Security y más). "
            "Un proyecto de DERENZIN S.A.S."
        )
        imagen_og_edicion = items_ordenados[0].get("imagen_local") or "/assets/logo-derenzin.png"
        pagina_dia = bs.render_pagina_archivo_dia(fecha, subtitulo, items_html, descripcion_edicion, imagen_og_edicion)
        (ARCHIVO_DIR / f"{fecha}.html").write_text(pagina_dia, encoding="utf-8")
        log(f"Escrito {ARCHIVO_DIR / f'{fecha}.html'} ({len(items_ordenados)} noticia(s)).")
    finally:
        bs.categorizar = _orig_categorizar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada; solo lista qué movería.")
    args = parser.parse_args()

    log("Auditando site/archivo/*.html contra data/publicadas.json (fecha real de cada ítem)...")
    desajustes = auditar()  # ruta_noticia -> fecha_real
    if not desajustes:
        log("Sin desajustes: cada noticia ya está archivada bajo su fecha de publicación real. Nada que hacer.")
        return

    log(f"{len(desajustes)} noticia(s) archivada(s) bajo un día distinto al de su publicación real:")
    for ruta, fecha_real in sorted(desajustes.items()):
        log(f"  {ruta}  ->  debería estar en archivo/{fecha_real}.html")

    if args.dry_run:
        log("(--dry-run: no se escribió nada.)")
        return

    categorias_actuales = mapa_categoria_actual()

    # Reconstruir cada ítem afectado desde su página de detalle ya publicada.
    items_afectados: dict[str, dict] = {}  # ruta_noticia -> item
    for ruta in desajustes:
        item = leer_item_publicado(ruta, categoria=categorias_actuales.get(ruta))
        if item is not None:
            items_afectados[ruta] = item

    # Días a regenerar: cada archivo de origen que tiene al menos un ítem
    # movido, más cada fecha de destino a la que algo se mueve.
    fechas_destino_afectadas = set(desajustes.values())
    fechas_origen_con_movimiento = set()
    for p in sorted(ARCHIVO_DIR.glob("*.html")):
        if p.stem == "index":
            continue
        contenido = p.read_text(encoding="utf-8")
        rutas = set(RE_HREF_NOTICIA.findall(contenido))
        if rutas & set(desajustes.keys()):
            fechas_origen_con_movimiento.add(p.stem)

    todas_las_fechas_a_tocar = fechas_origen_con_movimiento | fechas_destino_afectadas
    log(f"Días a regenerar: {sorted(todas_las_fechas_a_tocar)}")

    for fecha in sorted(todas_las_fechas_a_tocar):
        ruta_archivo_fecha = ARCHIVO_DIR / f"{fecha}.html"
        items_del_dia: list[dict] = []

        if ruta_archivo_fecha.exists():
            contenido = ruta_archivo_fecha.read_text(encoding="utf-8")
            rutas_actuales = sorted(set(RE_HREF_NOTICIA.findall(contenido)))
        else:
            rutas_actuales = []

        for ruta in rutas_actuales:
            fecha_real_item = desajustes.get(ruta)
            if fecha_real_item is not None and fecha_real_item != fecha:
                continue  # este ítem se va a otro día, no se incluye acá
            item = items_afectados.get(ruta) or leer_item_publicado(ruta, categoria=categorias_actuales.get(ruta))
            if item is not None:
                items_del_dia.append(item)

        # Agregar los ítems que llegan de OTROS días hacia este.
        for ruta, fecha_real_item in desajustes.items():
            if fecha_real_item == fecha and ruta not in rutas_actuales:
                item = items_afectados.get(ruta)
                if item is not None:
                    items_del_dia.append(item)

        regenerar_dia(fecha, items_del_dia)

    # Índice de archivo y sitemap, para que reflejen los archivos nuevos/actualizados.
    dias_existentes = sorted({p.stem for p in ARCHIVO_DIR.glob("*.html") if p.stem != "index"})
    (ARCHIVO_DIR / "index.html").write_text(bs.render_archivo_index(dias_existentes), encoding="utf-8")
    log(f"Escrito {ARCHIVO_DIR / 'index.html'} ({len(dias_existentes)} edición/ediciones listadas).")

    bs.generar_sitemap()

    # Blindaje: aunque este script ya arma cada tarjeta con categoria-badge y
    # eyebrow-categoria desde el mismo `categoria` (ver render_tarjeta_html),
    # correr esto deja los días regenerados consistentes con el resto del
    # sitio usando exactamente la misma verificación que build_site.py aplica
    # al final de cada build normal.
    bs.reparar_badges_categoria_en_sitio()

    log("Listo.")


if __name__ == "__main__":
    main()
