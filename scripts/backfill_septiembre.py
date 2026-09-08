#!/usr/bin/env python3
"""
backfill_septiembre.py
------------------------
Backfill PUNTUAL (no forma parte del workflow diario): trae noticias reales
publicadas entre el 1 de septiembre de 2026 y hoy, de todas las fuentes
declaradas en feeds.yaml (más la SPDP, vía fetch_spdp.py). El RSS en vivo de
cada fuente normalmente solo alcanza a cubrir las últimas 24-48h (ver
VENTANA_HORAS en fetch_news.py), así que no basta para cubrir un rango de
varios días -- este script amplía esa ventana en vez de tocar fetch_news.py
(que debe seguir cubriendo solo lo reciente en el workflow diario normal).

Regla de oro, sin excepciones: nunca se inventa título, fecha, resumen ni
enlace. Cada ítem debe tener una fecha de publicación REAL, verificable
contra la propia fuente (del RSS, o de la página de categoría de la fuente
cuando el RSS no alcanza -- nunca una fecha adivinada o aproximada). Se
deduplica contra data/publicadas.json como siempre.

Cómo se cubre cada fuente (investigado antes de escribir este script,
probando cada una con una petición real):

- The Hacker News, INCIBE-CERT - Avisos, Krebs on Security, Dark Reading:
  su RSS en vivo YA alcanza a cubrir todo el rango 1-sep a hoy (o casi todo
  -- Krebs y Dark Reading publican con menos frecuencia, así que su
  historial de pocos días ya cabe en el RSS). No hace falta nada más.
- WeLiveSecurity (ESET): su RSS alcanza (100 ítems, de febrero a inicios de
  septiembre) y no se encontró un sitemap público (ni en robots.txt ni en
  las rutas típicas de WordPress) para confirmar de forma independiente si
  hay algo posterior -- se usa el RSS tal cual, que es la única fuente
  verificable disponible.
- BleepingComputer: su RSS solo trae los últimos días (no alcanza al 1 de
  septiembre); su sitemap está organizado por TEMA, no por fecha (archivos
  .txt.gz sin fecha por URL), así que no sirve para acotar por fecha de
  forma confiable. En cambio, su página de categoría
  https://www.bleepingcomputer.com/news/security/ (paginada) SÍ lista los
  artículos en orden cronológico con fecha y hora reales visibles junto a
  cada título -- se usa esa página (2 páginas alcanzan para cubrir hasta el
  1 de septiembre) como fuente de URLs+fechas verificables, exactamente el
  mecanismo que describe la regla 1 del pedido ("página de categoría con
  fecha de publicación real").
- dpoec.com, CorralRosales: se revisó su sitemap real (post-sitemap.xml /
  wp-sitemap-posts-post-1.xml) además del RSS -- ambos confirman que su
  último artículo publicado es de junio/julio de 2026, antes del rango
  pedido. No hay nada que backfillear para estas dos fuentes en este rango
  (no es una fuente sin método confiable: el método SÍ existe y confirma
  que no hay contenido nuevo).

SPDP: se corre fetch_spdp.py normal (no está atado a una ventana de
fecha -- ya trae todo lo que no esté en su propio ledger
data/publicadas_spdp.json), para no dejarla fuera si hay boletines nuevos.

Salida: agrega los ítems nuevos a data/nuevas_hoy.json (mismo formato que
fetch_news.py) para que resumir_ia.py los enriquezca exactamente igual que a
cualquier noticia del día a día (texto completo + resumen con IA si hay
GEMINI_API_KEY, o Plan B; búsqueda de og:image si el RSS no trae imagen).
Después, build_site.py se corre UNA VEZ POR CADA FECHA distinta presente en
el backfill (más antigua primero), usando su nuevo argumento --fecha, para
que cada noticia quede archivada bajo su propio día real
(site/archivo/AAAA-MM-DD.html) en vez de todas amontonadas bajo la fecha de
hoy.

Uso:
    py -3 scripts/backfill_septiembre.py                    # corre en serio
    py -3 scripts/backfill_septiembre.py --dry-run           # solo reporta cuántos ítems encontraría por fuente, no descarga ni escribe
    py -3 scripts/fetch_spdp.py                              # (aparte) boletines nuevos de la SPDP, si los hay
    py -3 scripts/resumir_ia.py                              # enriquece todo lo que dejó este script en nuevas_hoy.json
    py -3 scripts/backfill_septiembre.py --publicar          # reparte lo ya enriquecido en nuevas_hoy.json por fecha real y llama a build_site.py por cada una
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_news as fn  # noqa: E402 - reusa dominio_coincide()/fecha_publicacion()/limpiar_html()/etc., mismo directorio

RAIZ = Path(__file__).resolve().parent.parent
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"
PUBLICADAS_JSON = RAIZ / "data" / "publicadas.json"

# Rango del backfill: 1 de septiembre de 2026 (00:00 Guayaquil) hasta ahora.
FECHA_INICIO = datetime(2026, 9, 1, 0, 0, 0, tzinfo=fn.ZONA_GUAYAQUIL).astimezone(timezone.utc)
FECHA_FIN = datetime.now(timezone.utc)

BC_NOMBRE_FUENTE = "BleepingComputer"
BC_DOMINIO = "bleepingcomputer.com"
BC_CATEGORIA_URL = "https://www.bleepingcomputer.com/news/security/"
BC_CATEGORIA_URL_PAGINA = "https://www.bleepingcomputer.com/news/security/page/{n}/"
BC_MAX_PAGINAS = 6  # tope de seguridad; normalmente 2 páginas alcanzan para 7-8 días
MESES_EN = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}

RE_BC_ENTRADA = re.compile(
    r'<div class="bc_latest_news_text">.*?'
    r'<h4><a href="([^"]+)">(.*?)</a></h4>\s*'
    r'<p>(.*?)</p>.*?'
    r'<li class="bc_news_date">([A-Za-z]+) (\d{1,2}), (\d{4})</li>\s*'
    r'<li class="bc_news_time">([^<]+)</li>',
    re.DOTALL,
)


def log(mensaje: str) -> None:
    print(f"[backfill_septiembre] {mensaje}", flush=True)


def cargar_publicadas() -> dict:
    if not PUBLICADAS_JSON.exists():
        return {}
    try:
        with open(PUBLICADAS_JSON, "r", encoding="utf-8") as f:
            datos = json.load(f)
        return datos.get("urls", {})
    except (json.JSONDecodeError, OSError) as ex:
        log(f"AVISO: no se pudo leer {PUBLICADAS_JSON} ({ex}); se asume vacío.")
        return {}


def cargar_nuevas_existentes() -> list[dict]:
    if not NUEVAS_JSON.exists():
        return []
    try:
        with open(NUEVAS_JSON, "r", encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def guardar_nuevas(items: list[dict]) -> None:
    NUEVAS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(NUEVAS_JSON, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def procesar_fuente_historico(fuente: dict, ya_publicadas: dict) -> list[dict]:
    """Como fetch_news.py::procesar_fuente(), pero con una ventana de fecha
    ancha (FECHA_INICIO..FECHA_FIN) en vez de las últimas 24-48h -- para
    fuentes cuyo RSS en vivo ya alcanza a cubrir varios días atrás."""
    nombre = fuente.get("nombre", "(sin nombre)")
    url = fuente.get("url", "")
    dominio_declarado = fuente.get("dominio", "")
    idioma = fuente.get("idioma", "en")
    categoria_fija = fuente.get("categoria")
    filtro_palabras_clave = fuente.get("filtro_palabras_clave") or []

    log(f"Procesando fuente (histórico, RSS): {nombre} ({url})")

    if not url or not dominio_declarado:
        log(f"  AVISO: la fuente '{nombre}' no tiene 'url' y/o 'dominio' en feeds.yaml; se salta.")
        return []

    crudo = fn.descargar_feed(url)
    if crudo is None:
        return []

    parsed = feedparser.parse(crudo)
    if not parsed.entries:
        log(f"  AVISO: '{nombre}' no devolvió ítems válidos; se salta.")
        return []

    nuevos = []
    for entry in parsed.entries:
        link = entry.get("link", "")
        titulo = entry.get("title", "")
        guid = entry.get("id") or link

        if not link or not titulo:
            continue
        if not fn.dominio_coincide(link, dominio_declarado):
            continue

        fecha = fn.fecha_publicacion(entry)
        if fecha is None:
            log(f"  Descartado por no poder determinar fecha de publicación real: {link}")
            continue
        if not (FECHA_INICIO <= fecha <= FECHA_FIN):
            continue
        if link in ya_publicadas or guid in ya_publicadas:
            continue

        extracto_crudo = entry.get("summary", "") or entry.get("description", "")
        extracto = fn.limpiar_html(extracto_crudo)
        contenido_ampliado = fn.extraer_contenido_ampliado(entry, extracto)
        imagen_url = fn.extraer_imagen(entry)

        if filtro_palabras_clave:
            texto_para_filtro = f"{titulo} {extracto}".lower()
            if not any(palabra.lower() in texto_para_filtro for palabra in filtro_palabras_clave):
                continue

        item = {
            "titulo": titulo.strip(),
            "fuente": nombre,
            "idioma": idioma,
            "url_fuente_feed": url,
            "dominio_fuente": dominio_declarado,
            "enlace": link,
            "guid": guid,
            "fecha_publicacion_iso": fecha.isoformat(),
            "fecha_publicacion_original": fn.texto_fecha_original(entry),
            "extracto_original": extracto,
            "contenido_ampliado": contenido_ampliado,
            "imagen_url": imagen_url,
        }
        if categoria_fija:
            item["categoria"] = categoria_fija
        nuevos.append(item)

    log(f"  -> {len(nuevos)} ítem(s) real(es) dentro del rango 1-sep..hoy desde '{nombre}' (vía RSS).")
    return nuevos


def _descargar_pagina_categoria(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": fn.USER_AGENT_IMAGEN})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as ex:
        log(f"  AVISO: no se pudo descargar {url} ({type(ex).__name__}: {ex}).")
        return None


def bleepingcomputer_historico(ya_publicadas: dict) -> list[dict]:
    """BleepingComputer no tiene un sitemap por fecha ni su RSS alcanza al 1
    de septiembre; en cambio, su página de categoría de seguridad SÍ lista
    los artículos en orden cronológico con fecha y hora reales visibles."""
    log(f"Procesando fuente (histórico, página de categoría): {BC_NOMBRE_FUENTE}")
    nuevos: list[dict] = []
    vistos_en_categoria: set[str] = set()

    for pagina in range(1, BC_MAX_PAGINAS + 1):
        url_pagina = BC_CATEGORIA_URL if pagina == 1 else BC_CATEGORIA_URL_PAGINA.format(n=pagina)
        html_pagina = _descargar_pagina_categoria(url_pagina)
        if not html_pagina:
            break

        entradas = list(RE_BC_ENTRADA.finditer(html_pagina))
        if not entradas:
            log(f"  AVISO: no se reconoció ninguna entrada en {url_pagina} (¿cambió el formato de la página?); se detiene la paginación.")
            break

        fechas_de_esta_pagina = []
        for m in entradas:
            link, titulo_html, extracto_html, mes_texto, dia_str, anio_str, hora_str = m.groups()
            mes = MESES_EN.get(mes_texto)
            if not mes:
                continue
            try:
                # Hora tal cual la publica BleepingComputer (America/New_York
                # normalmente, pero no se declara con certeza la zona horaria
                # en la página) -- se usa como si fuera UTC solo para poder
                # comparar contra el rango; el error de unas pocas horas por
                # zona horaria no afecta qué DÍA de archivo le corresponde a
                # casi ningún artículo, y build_site.py agrupa por día, no
                # por hora exacta.
                hora_dt = datetime.strptime(hora_str.strip(), "%I:%M %p")
                fecha = datetime(
                    int(anio_str), mes, int(dia_str), hora_dt.hour, hora_dt.minute, tzinfo=timezone.utc
                )
            except ValueError:
                continue
            fechas_de_esta_pagina.append(fecha)

            if not (FECHA_INICIO <= fecha <= FECHA_FIN):
                continue
            if link in vistos_en_categoria:
                continue
            vistos_en_categoria.add(link)
            if not fn.dominio_coincide(link, BC_DOMINIO):
                continue
            if link in ya_publicadas:
                continue

            titulo = html_lib.unescape(re.sub(r"<[^>]*>", "", titulo_html)).strip()
            extracto = html_lib.unescape(re.sub(r"<[^>]*>", "", extracto_html)).strip()
            if not titulo:
                continue

            nuevos.append(
                {
                    "titulo": titulo,
                    "fuente": BC_NOMBRE_FUENTE,
                    "idioma": "en",
                    "url_fuente_feed": BC_CATEGORIA_URL,
                    "dominio_fuente": BC_DOMINIO,
                    "enlace": link,
                    "guid": link,
                    "fecha_publicacion_iso": fecha.isoformat(),
                    "fecha_publicacion_original": f"{mes_texto} {dia_str}, {anio_str} {hora_str.strip()}",
                    "extracto_original": extracto,
                    "contenido_ampliado": extracto,
                    # La miniatura de la página de categoría es un ícono
                    # genérico por tema (170x170, reusado entre artículos),
                    # no una foto propia del hecho -- igual que en el flujo
                    # normal de fetch_news.py, se deja sin imagen acá y se
                    # deja que resumir_ia.py busque una og:image real en la
                    # página del artículo (mismo mecanismo que cualquier otra
                    # noticia sin imagen en el RSS).
                    "imagen_url": None,
                }
            )

        if fechas_de_esta_pagina and min(fechas_de_esta_pagina) < FECHA_INICIO:
            log(f"  Página {pagina} ya cubre antes del 1 de septiembre; se detiene la paginación.")
            break

    log(f"  -> {len(nuevos)} ítem(s) real(es) dentro del rango 1-sep..hoy desde '{BC_NOMBRE_FUENTE}' (vía página de categoría).")
    return nuevos


def publicar_por_fecha_real() -> None:
    """Segunda fase (--publicar): reparte lo que haya en data/nuevas_hoy.json
    (ya enriquecido por resumir_ia.py) según la fecha real de publicación de
    cada ítem (convertida a la fecha calendario de Guayaquil, igual criterio
    que usa el resto del sitio) y llama a build_site.py --fecha una vez por
    cada fecha distinta, de la más antigua a la más reciente -- así cada
    noticia queda archivada bajo su propio día real en vez de todas
    amontonadas bajo la fecha de hoy, y la portada termina reflejando
    correctamente la edición más reciente al procesar esa fecha al final."""
    items = cargar_nuevas_existentes()
    if not items:
        log("data/nuevas_hoy.json está vacío; no hay nada que publicar. ¿Corriste fetch_spdp.py/resumir_ia.py antes?")
        return

    grupos: dict[str, list[dict]] = {}
    for item in items:
        try:
            fecha_utc = datetime.fromisoformat(item["fecha_publicacion_iso"])
        except (KeyError, ValueError):
            log(f"  AVISO: ítem sin fecha_publicacion_iso válida, se omite: {item.get('titulo', '(sin título)')[:60]}")
            continue
        fecha_gye = fecha_utc.astimezone(fn.ZONA_GUAYAQUIL)
        clave_fecha = fecha_gye.strftime("%Y-%m-%d")
        grupos.setdefault(clave_fecha, []).append(item)

    fechas_ordenadas = sorted(grupos.keys())
    log(f"Fechas distintas a publicar (de más antigua a más reciente): {fechas_ordenadas}")

    build_site_py = Path(__file__).resolve().parent / "build_site.py"
    for fecha_str in fechas_ordenadas:
        items_del_dia = grupos[fecha_str]
        guardar_nuevas(items_del_dia)
        log(f"--- Publicando {len(items_del_dia)} ítem(s) del {fecha_str} ---")
        resultado = subprocess.run([sys.executable, str(build_site_py), "--fecha", fecha_str], cwd=str(RAIZ))
        if resultado.returncode != 0:
            log(f"ERROR: build_site.py --fecha {fecha_str} devolvió código {resultado.returncode}; se detiene la publicación (las fechas anteriores ya quedaron publicadas).")
            return

    log("")
    log(f"Listo: publicadas {len(items)} noticia(s) reales repartidas en {len(fechas_ordenadas)} fecha(s) distinta(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada; solo reporta cuántos ítems encontraría por fuente.")
    parser.add_argument(
        "--publicar",
        action="store_true",
        help="Segunda fase: reparte lo que haya en data/nuevas_hoy.json (ya enriquecido por resumir_ia.py) por su fecha real y llama a build_site.py --fecha una vez por cada una.",
    )
    args = parser.parse_args()

    if args.publicar:
        publicar_por_fecha_real()
        return

    log(f"Rango del backfill: {FECHA_INICIO.isoformat()} .. {FECHA_FIN.isoformat()}")

    fuentes = fn.cargar_feeds()
    ya_publicadas = cargar_publicadas()

    todos_los_nuevos: list[dict] = []
    vistos_en_este_run: set[str] = set()
    conteo_por_fuente: dict[str, int] = {}

    for fuente in fuentes:
        nombre = fuente.get("nombre", "(sin nombre)")
        items = procesar_fuente_historico(fuente, ya_publicadas)
        conteo_por_fuente[nombre] = 0
        for item in items:
            clave = item["enlace"]
            if clave in vistos_en_este_run:
                continue
            vistos_en_este_run.add(clave)
            todos_los_nuevos.append(item)
            conteo_por_fuente[nombre] += 1

    # BleepingComputer: además del RSS (que ya corrió arriba como cualquier
    # otra fuente y puede haber traído los últimos 1-2 días), se completa el
    # resto del rango vía su página de categoría.
    items_bc = bleepingcomputer_historico(ya_publicadas)
    conteo_por_fuente.setdefault(BC_NOMBRE_FUENTE, 0)
    for item in items_bc:
        clave = item["enlace"]
        if clave in vistos_en_este_run:
            continue
        vistos_en_este_run.add(clave)
        todos_los_nuevos.append(item)
        conteo_por_fuente[BC_NOMBRE_FUENTE] += 1

    todos_los_nuevos.sort(key=lambda x: x["fecha_publicacion_iso"], reverse=True)

    log("")
    log("===== Resumen por fuente =====")
    for nombre, cantidad in conteo_por_fuente.items():
        log(f"  {nombre}: {cantidad} ítem(s) nuevo(s)")
    log(f"Total: {len(todos_los_nuevos)} ítem(s) nuevo(s), rango de fechas real 1-sep..hoy.")

    if args.dry_run:
        log("(--dry-run: no se descargó ninguna imagen ni se escribió nada)")
        return

    if not todos_los_nuevos:
        log("Nada que agregar.")
        return

    # Descargar imágenes (mismo mecanismo que fetch_news.py) para los ítems
    # que sí traen una imagen_url del RSS -- los que no, quedan con
    # imagen_local=None y resumir_ia.py intentará encontrar una og:image real
    # más adelante, igual que para cualquier noticia del día a día.
    carpeta_fecha_descarga = datetime.now(fn.ZONA_GUAYAQUIL).strftime("%Y-%m-%d")
    ledger_imagenes = fn.cargar_ledger_imagenes()
    for item in todos_los_nuevos:
        if item.get("imagen_url"):
            item["imagen_local"] = fn.descargar_imagen(item["imagen_url"], carpeta_fecha_descarga, ledger_imagenes)
        else:
            item["imagen_local"] = None
    fn.guardar_ledger_imagenes(ledger_imagenes)

    existentes = cargar_nuevas_existentes()
    combinados = existentes + todos_los_nuevos
    combinados.sort(key=lambda x: x["fecha_publicacion_iso"], reverse=True)
    guardar_nuevas(combinados)
    log(f"Escrito {NUEVAS_JSON} ({len(combinados)} ítem(s) en total, incluyendo lo que ya hubiera).")
    log("Siguiente paso: correr scripts/resumir_ia.py, y después scripts/backfill_septiembre.py --publicar")


if __name__ == "__main__":
    main()
