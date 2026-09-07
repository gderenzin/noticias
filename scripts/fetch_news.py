#!/usr/bin/env python3
"""
fetch_news.py
-------------
Descarga los feeds RSS declarados en feeds.yaml, se queda solo con los ítems
publicados en las últimas 24-48 horas, descarta cualquier ítem cuyo dominio de
enlace no coincida con el dominio declarado de la fuente, y descarta los que ya
estén en data/publicadas.json (el ledger de lo ya publicado).

Regla de oro: nunca se genera contenido. Este script SOLO copia campos que
vienen tal cual del RSS (título, fuente, fecha, enlace, extracto). No inventa
nada. Si un feed falla, se salta y se sigue con los demás. Si ningún feed trae
novedades, no se falla el proceso: simplemente no hay nada nuevo que publicar.

Salida: data/nuevas_hoy.json -> lista de ítems nuevos, listos para que
build_site.py los convierta en HTML.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import yaml

RAIZ = Path(__file__).resolve().parent.parent
FEEDS_YAML = RAIZ / "feeds.yaml"
PUBLICADAS_JSON = RAIZ / "data" / "publicadas.json"
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"

VENTANA_HORAS = 48  # tomamos ítems de las últimas 24-48h; usamos 48 para no dejar huecos
TIMEOUT_SEGUNDOS = 20
USER_AGENT = (
    "Mozilla/5.0 (compatible; PeriodicoCiberseguridadBot/1.0; "
    "+https://github.com/) NewsAggregator/1.0"
)


def log(mensaje: str) -> None:
    print(f"[fetch_news] {mensaje}", flush=True)


def cargar_feeds() -> list[dict]:
    if not FEEDS_YAML.exists():
        log(f"ERROR CRÍTICO: no existe {FEEDS_YAML}. Abortando.")
        sys.exit(1)
    with open(FEEDS_YAML, "r", encoding="utf-8") as f:
        contenido = yaml.safe_load(f) or {}
    fuentes = contenido.get("fuentes") or []
    if not fuentes:
        log("ERROR CRÍTICO: feeds.yaml no define ninguna fuente. Abortando.")
        sys.exit(1)
    return fuentes


def cargar_publicadas() -> dict:
    if not PUBLICADAS_JSON.exists():
        return {}
    try:
        with open(PUBLICADAS_JSON, "r", encoding="utf-8") as f:
            datos = json.load(f)
        # formato: {"urls": {url: {...metadata...}}}
        return datos.get("urls", {})
    except (json.JSONDecodeError, OSError) as ex:
        log(f"AVISO: no se pudo leer {PUBLICADAS_JSON} ({ex}); se asume vacío.")
        return {}


def descargar_feed(url: str) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEGUNDOS) as resp:
            if resp.status != 200:
                log(f"  AVISO: HTTP {resp.status} al descargar {url}; se salta esta fuente.")
                return None
            return resp.read()
    except urllib.error.HTTPError as ex:
        log(f"  AVISO: HTTPError {ex.code} al descargar {url}; se salta esta fuente.")
    except urllib.error.URLError as ex:
        log(f"  AVISO: URLError ({ex.reason}) al descargar {url}; se salta esta fuente.")
    except TimeoutError:
        log(f"  AVISO: timeout al descargar {url}; se salta esta fuente.")
    except Exception as ex:  # noqa: BLE001 - un feed caído nunca debe tumbar el proceso
        log(f"  AVISO: error inesperado ({type(ex).__name__}: {ex}) al descargar {url}; se salta esta fuente.")
    return None


def normalizar_dominio(host: str) -> str:
    host = (host or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


def dominio_coincide(link: str, dominio_declarado: str) -> bool:
    host_enlace = normalizar_dominio(urlparse(link).netloc)
    dominio_declarado = normalizar_dominio(dominio_declarado)
    if not host_enlace or not dominio_declarado:
        return False
    return host_enlace == dominio_declarado or host_enlace.endswith("." + dominio_declarado)


def limpiar_html(texto: str) -> str:
    """Quita etiquetas HTML y decodifica entidades (&nbsp;, &amp;, etc.) de un
    extracto de RSS, sin dependencias externas. El texto resultante queda en
    texto plano; build_site.py se encarga de volver a escaparlo para HTML."""
    import html
    import re

    if not texto:
        return ""
    sin_tags = re.sub(r"<[^>]+>", " ", texto)
    decodificado = html.unescape(sin_tags)
    sin_espacios = re.sub(r"\s+", " ", decodificado).strip()
    return sin_espacios


EXTENSIONES_IMAGEN = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif")


def extraer_imagen(entry) -> str | None:
    """Devuelve la URL de la imagen propia del ítem si el RSS trae una
    (etiqueta <enclosure> o <media:content>/<media:thumbnail>), o None si no
    trae ninguna. Nunca se inventa una imagen: si no hay nada estructurado en
    el RSS, build_site.py usará un ícono genérico por categoría en su lugar.

    A propósito NO se exige que el dominio de la imagen coincida con el
    dominio declarado de la fuente (a diferencia del enlace del artículo):
    muchos feeds legítimos sirven sus imágenes desde un CDN o servicio de
    imágenes distinto (p.ej. The Hacker News enlaza su imagen desde
    blogger.googleusercontent.com). La procedencia de la imagen sigue estando
    atada al ítem de RSS ya verificado; solo se exige que sea una URL
    http/https válida.
    """
    candidatos = []

    for enc in entry.get("enclosures", []) or []:
        candidatos.append((enc.get("href") or enc.get("url") or "", (enc.get("type") or "").lower(), ""))

    for campo in ("media_content", "media_thumbnail"):
        for m in entry.get(campo, []) or []:
            candidatos.append((m.get("url") or "", (m.get("type") or "").lower(), (m.get("medium") or "").lower()))

    for url, tipo, medio in candidatos:
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        es_imagen = tipo.startswith("image/") or medio == "image" or url.lower().split("?")[0].endswith(EXTENSIONES_IMAGEN)
        if es_imagen:
            return url

    return None


def fecha_publicacion(entry) -> datetime | None:
    """Devuelve la fecha de publicación del ítem como datetime aware en UTC, o None si no se puede determinar."""
    for campo in ("published_parsed", "updated_parsed"):
        st = entry.get(campo)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def texto_fecha_original(entry) -> str:
    """Guarda el string de fecha tal cual viene del RSS, para mostrarlo sin reinterpretar."""
    return entry.get("published") or entry.get("updated") or ""


def procesar_fuente(fuente: dict, ahora_utc: datetime, ya_publicadas: dict) -> list[dict]:
    nombre = fuente.get("nombre", "(sin nombre)")
    url = fuente.get("url", "")
    dominio_declarado = fuente.get("dominio", "")
    idioma = fuente.get("idioma", "en")

    log(f"Procesando fuente: {nombre} ({url})")

    if not url or not dominio_declarado:
        log(f"  AVISO: la fuente '{nombre}' no tiene 'url' y/o 'dominio' en feeds.yaml; se salta.")
        return []

    crudo = descargar_feed(url)
    if crudo is None:
        return []

    parsed = feedparser.parse(crudo)

    if not parsed.entries:
        motivo = getattr(parsed, "bozo_exception", None)
        log(f"  AVISO: '{nombre}' no devolvió ítems válidos (bozo={parsed.bozo}, {motivo}); se salta.")
        return []

    if parsed.bozo:
        log(f"  AVISO: '{nombre}' marcó bozo=True ({getattr(parsed, 'bozo_exception', '')}) pero sí trae ítems; se continúa con precaución.")

    limite = ahora_utc - timedelta(hours=VENTANA_HORAS)
    nuevos = []

    for entry in parsed.entries:
        link = entry.get("link", "")
        titulo = entry.get("title", "")
        guid = entry.get("id") or link

        if not link or not titulo:
            log(f"  Descartado (sin título o sin enlace): {guid!r}")
            continue

        if not dominio_coincide(link, dominio_declarado):
            log(f"  Descartado por dominio no coincidente: {link} (se esperaba dominio '{dominio_declarado}')")
            continue

        fecha = fecha_publicacion(entry)
        if fecha is None:
            log(f"  Descartado por no poder determinar fecha de publicación: {link}")
            continue

        if fecha < limite:
            continue  # más viejo que la ventana de 24-48h, no es descarte por error, simplemente no es "de hoy"

        if fecha > ahora_utc + timedelta(hours=2):
            log(f"  Descartado por fecha futura sospechosa (feed adelantado): {link}")
            continue

        if link in ya_publicadas or guid in ya_publicadas:
            continue  # ya publicado en un run anterior

        extracto_crudo = entry.get("summary", "") or entry.get("description", "")
        extracto = limpiar_html(extracto_crudo)
        imagen_url = extraer_imagen(entry)

        nuevos.append(
            {
                "titulo": titulo.strip(),
                "fuente": nombre,
                "idioma": idioma,
                "url_fuente_feed": url,
                "dominio_fuente": dominio_declarado,
                "enlace": link,
                "guid": guid,
                "fecha_publicacion_iso": fecha.isoformat(),
                "fecha_publicacion_original": texto_fecha_original(entry),
                "extracto_original": extracto,
                "imagen_url": imagen_url,
            }
        )

    log(f"  -> {len(nuevos)} ítem(s) nuevo(s) dentro de la ventana de {VENTANA_HORAS}h desde '{nombre}'.")
    return nuevos


def main() -> None:
    ahora_utc = datetime.now(timezone.utc)
    log(f"Inicio de ejecución: {ahora_utc.isoformat()}")

    fuentes = cargar_feeds()
    ya_publicadas = cargar_publicadas()

    todos_los_nuevos: list[dict] = []
    vistos_en_este_run: set[str] = set()

    for fuente in fuentes:
        items = procesar_fuente(fuente, ahora_utc, ya_publicadas)
        for item in items:
            clave = item["enlace"]
            if clave in vistos_en_este_run:
                continue  # por si dos feeds distintos referencian el mismo artículo
            vistos_en_este_run.add(clave)
            todos_los_nuevos.append(item)

    # Orden: más reciente primero
    todos_los_nuevos.sort(key=lambda x: x["fecha_publicacion_iso"], reverse=True)

    NUEVAS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(NUEVAS_JSON, "w", encoding="utf-8") as f:
        json.dump(todos_los_nuevos, f, ensure_ascii=False, indent=2)

    if not todos_los_nuevos:
        log("Sin novedades hoy: ningún feed trajo ítems nuevos dentro de la ventana. No se publica nada nuevo.")
    else:
        log(f"Total de ítems nuevos a publicar: {len(todos_los_nuevos)}.")

    log("Fin de ejecución de fetch_news.py.")


if __name__ == "__main__":
    main()
