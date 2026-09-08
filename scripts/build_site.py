#!/usr/bin/env python3
"""
build_site.py
--------------
Toma data/nuevas_hoy.json (generado por fetch_news.py) y:

  1. Traduce al español el título y el resumen de cada ítem (ver "Sobre la
     traducción" abajo). Todo el sitio —títulos, resúmenes y textos de
     interfaz— se muestra en español, incluso si la fuente original está en
     inglés.
  2. Elige una imagen para cada noticia: si fetch_news.py logró descargar la
     imagen propia del RSS (<enclosure> o <media:content>) a
     site/imagenes/AAAA-MM-DD/, la usa desde esa copia local; si no hay
     imagen o la descarga falló, muestra un ícono genérico ilustrativo según
     la categoría de la noticia (nunca se genera ni se inventa una foto).
  3. Genera/actualiza site/index.html (portada de hoy).
  4. Crea site/archivo/AAAA-MM-DD.html con la edición del día.
  5. Regenera site/archivo/index.html (listado de ediciones anteriores).
  6. Actualiza data/publicadas.json (el ledger) con los enlaces publicados.

Sobre la traducción (DeepL):
  Se traduce con la API de DeepL usando la clave en la variable de entorno
  DEEPL_API_KEY (configurada como Secret de GitHub Actions — ver README.md).
  Si la clave no está configurada (p.ej. al correr en local sin ella) o la
  llamada a la API falla por cualquier motivo (red, cuota agotada, timeout),
  NUNCA se inventa una traducción: esa noticia en particular se muestra
  citando el título/extracto original entre comillas con una nota aclaratoria
  en español, en vez de fabricar un texto. La traducción nunca agrega cifras,
  nombres ni hechos: solo traduce el título y el extracto tal cual vienen del
  RSS (ver fetch_news.py).

Sobre las imágenes:
  Las imágenes reales ya vienen descargadas y guardadas por fetch_news.py
  (no se hotlinkea a la URL externa — ver ese script para el porqué). Aquí
  solo se referencian con una ruta local (absoluta desde la raíz del sitio,
  p.ej. "/imagenes/2026-09-06/abc123.jpg"), con "loading=lazy" y
  "decoding=async" para no frenar la carga de la página. No se re-codifican
  ni redimensionan (evita depender de una librería pesada como Pillow) — se
  guardan tal cual las entrega la fuente. Los íconos genéricos son SVG en
  línea: no generan ninguna petición de red. Toda imagen (real o ícono)
  lleva encima una insignia de color con el nombre de la categoría.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape, unescape
from pathlib import Path

from texto import acotar_parrafos, fuente_parece_incompleta, primeras_oraciones, rematar_final, truncar

RAIZ = Path(__file__).resolve().parent.parent
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"
PUBLICADAS_JSON = RAIZ / "data" / "publicadas.json"
# Lista de los últimos N ítems de la sección "Protección de Datos" (ver
# main() y render_pagina_seccion_proteccion_datos()): a diferencia de
# site/index.html (que solo muestra la última edición del día), esta
# sección tiene tan poco volumen que "solo lo de hoy" quedaría casi
# siempre vacía -- por eso se mantiene esta lista propia, independiente
# del ciclo diario de ciberseguridad, para que la portada de la sección
# siempre muestre algo aunque hoy no haya habido ninguna novedad.
PROTECCION_DATOS_JSON = RAIZ / "data" / "proteccion_datos_recientes.json"
MAX_RECIENTES_PROTECCION_DATOS = 20
SITE_DIR = RAIZ / "site"
ARCHIVO_DIR = SITE_DIR / "archivo"
NOTICIA_DIR = SITE_DIR / "noticia"
PROTECCION_DATOS_DIR = SITE_DIR / "proteccion-datos"

LIMITE_RESUMEN_AMPLIADO = 2000  # caracteres; nunca se muestra el artículo completo
MAX_PARRAFOS_AMPLIADO = 5

# Guayaquil = UTC-5 todo el año (Ecuador no usa horario de verano)
ZONA_GUAYAQUIL = timezone(timedelta(hours=-5))

# --- SEO / seguridad -------------------------------------------------------
# Dominio público del sitio (para canonical, Open Graph, JSON-LD y sitemap.xml).
SITIO_BASE_URL = "https://noticias.derenzin.com"

# Content-Security-Policy vía <meta>: solo el propio dominio y Google Fonts
# (única dependencia externa). No hay ningún <script> en el sitio aparte de
# los bloques JSON-LD (application/ld+json, que los navegadores no tratan
# como "script" ejecutable a efectos de CSP), así que script-src puede ir en
# 'none'. Nota: frame-ancestors/sandbox no tienen efecto vía <meta> (los
# ignora el navegador) — si en el futuro este sitio se sirve detrás de algo
# que permita fijar headers HTTP de verdad, ahí sí conviene agregarlos.
POLITICA_SEGURIDAD_CONTENIDO = (
    "default-src 'self'; "
    "img-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "script-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
# Nota sobre script-src: pasó de 'none' a 'self' para poder cargar
# assets/compartir.js (botón "Copiar enlace", ver render_botones_compartir).
# Es el único script del sitio -- un archivo propio, sin inline ni terceros
# -- así que 'self' alcanza sin necesitar 'unsafe-inline' ni un nonce. Los
# enlaces "Compartir en <red>" son <a href> normales hacia el sitio externo
# correspondiente (navegación de usuario, no fetch/XHR), así que no requieren
# tocar default-src/connect-src.


def url_absoluta(ruta: str) -> str:
    """Convierte una ruta relativa a la raíz del sitio (o ya absoluta) en una
    URL completa con el dominio — necesaria para canonical/Open Graph/JSON-LD,
    que no pueden usar rutas relativas."""
    if ruta.startswith("http://") or ruta.startswith("https://"):
        return ruta
    return f"{SITIO_BASE_URL}/{ruta.lstrip('/')}"


def render_meta_seo(
    titulo: str,
    descripcion: str,
    ruta_canonica: str,
    ruta_imagen: str,
    tipo_og: str = "website",
    descripcion_social: str | None = None,
) -> str:
    """Bloque de <meta> compartido por las 4 plantillas: description,
    canonical, Open Graph y Twitter Card. `ruta_canonica` y `ruta_imagen`
    pueden ser relativas a la raíz del sitio (se resuelven con
    url_absoluta) o ya vernir absolutas.

    `descripcion_social` (opcional): si se pasa, es el texto que usan
    og:description/twitter:description en vez de `descripcion` -- para que
    la vista previa de WhatsApp/Facebook/Telegram pueda diferir del
    <meta name="description"> que ven los buscadores (ver
    render_pagina_noticia, que le agrega "Fuente: noticias.derenzin.com"
    adelante SOLO para redes sociales, sin tocar el description de SEO)."""
    url_canonica = url_absoluta(ruta_canonica)
    url_imagen = url_absoluta(ruta_imagen)
    desc = escape(descripcion, quote=True)
    desc_social = escape(descripcion_social if descripcion_social is not None else descripcion, quote=True)
    tit = escape(titulo, quote=True)
    return f"""  <meta name="description" content="{desc}">
  <link rel="canonical" href="{escape(url_canonica, quote=True)}">
  <meta property="og:type" content="{tipo_og}">
  <meta property="og:site_name" content="Noticias de Ciberseguridad">
  <meta property="og:title" content="{tit}">
  <meta property="og:description" content="{desc_social}">
  <meta property="og:url" content="{escape(url_canonica, quote=True)}">
  <meta property="og:image" content="{escape(url_imagen, quote=True)}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{tit}">
  <meta name="twitter:description" content="{desc_social}">
  <meta name="twitter:image" content="{escape(url_imagen, quote=True)}">"""


def render_json_ld_noticia(item: dict, titulo_mostrar: str, ruta_noticia: str, categoria: str) -> str:
    """Datos estructurados schema.org/NewsArticle para la página de detalle.
    Todos los campos salen tal cual de los datos ya verificados del RSS —
    nada inventado. "author" es la fuente original (no tenemos el nombre de
    un periodista individual en el RSS); "publisher" es este sitio.

    El campo "image" (la imagen DEL ARTÍCULO) sigue la misma regla que el
    og:image de más arriba: imagen real, o si no, el ícono de categoría --
    nunca el logo de la empresa. Distinto de "publisher.logo" (más abajo),
    que SÍ debe ser siempre el logo de la empresa -- ese es el logo de la
    organización que publica, no una imagen del artículo; no tiene el bug
    reportado y no cambia."""
    ruta_imagen = item.get("imagen_local") or f"/assets/og/{categoria}.png"
    datos = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": titulo_mostrar,
        "datePublished": item["fecha_publicacion_iso"],
        "dateModified": item["fecha_publicacion_iso"],
        "image": [url_absoluta(ruta_imagen)],
        "author": {"@type": "Organization", "name": item["fuente"]},
        "publisher": {
            "@type": "Organization",
            "name": "Noticias de Ciberseguridad — DERENZIN S.A.S.",
            "logo": {"@type": "ImageObject", "url": url_absoluta("/assets/logo-derenzin.png")},
        },
        "mainEntityOfPage": {"@type": "WebPage", "@id": url_absoluta(ruta_noticia)},
    }
    return f'  <script type="application/ld+json">{json.dumps(datos, ensure_ascii=False)}</script>'

AVISO_LEGAL = (
    "Este sitio agrega titulares y resúmenes de fuentes públicas verificadas; "
    "el contenido pertenece a sus autores originales. Consulta el enlace de "
    "cada fuente para leer el artículo completo."
)

MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

DEEPL_API_KEY = (os.environ.get("DEEPL_API_KEY") or "").strip()
DEEPL_TIMEOUT_SEGUNDOS = 15


# ---------------------------------------------------------------------------
# Categorías e íconos genéricos (solo se usan cuando el RSS NO trae imagen)
# ---------------------------------------------------------------------------

CATEGORIAS = {
    "ransomware": {
        "etiqueta": "Ransomware",
        "color": "#c0392b",
        "palabras": ["ransomware", "secuestro de datos"],
        "svg": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<rect x="5" y="11" width="14" height="9" rx="2"/>'
            '<path d="M8 11V7a4 4 0 0 1 8 0v4"/>'
            '<circle cx="12" cy="15.2" r="1.3" fill="currentColor" stroke="none"/>'
            "</svg>"
        ),
    },
    "phishing": {
        "etiqueta": "Phishing",
        "color": "#d97706",
        "palabras": ["phishing", "smishing", "vishing", "correo fraudulento", "suplantación", "suplantacion"],
        "svg": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<rect x="3" y="6" width="18" height="13" rx="2"/>'
            '<path d="M3 7.5l9 6 9-6"/>'
            '<path d="M12 13.5v4a2 2 0 0 0 3.6 1.2"/>'
            "</svg>"
        ),
    },
    "filtracion_datos": {
        "etiqueta": "Filtración de datos",
        "color": "#0f766e",
        "palabras": ["data breach", "breach", "leaked", " leak", "filtración", "filtracion", "brecha de datos", "expuestos", "expuesto"],
        "svg": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<ellipse cx="12" cy="6" rx="7" ry="2.4"/>'
            '<path d="M5 6v12c0 1.3 3.1 2.4 7 2.4s7-1.1 7-2.4V6"/>'
            '<path d="M5 12c0 1.3 3.1 2.4 7 2.4"/>'
            '<path d="M17.3 15.8c0 1.3-1.1 2.4-2.3 2.4s-2.3-1.1-2.3-2.4c0-1.5 2.3-3.8 2.3-3.8s2.3 2.3 2.3 3.8z" fill="currentColor" stroke="none"/>'
            "</svg>"
        ),
    },
    "vulnerabilidad": {
        "etiqueta": "Vulnerabilidad",
        "color": "#2563eb",
        "palabras": ["vulnerab", "cve-", "exploit", "zero-day", "0-day", "día cero", "dia cero", "parche", "flaw"],
        "svg": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M12 3l7 3v6c0 5-3.5 7.5-7 9-3.5-1.5-7-4-7-9V6l7-3z"/>'
            '<path d="M12 8v5"/>'
            '<circle cx="12" cy="16" r="0.9" fill="currentColor" stroke="none"/>'
            "</svg>"
        ),
    },
    "malware": {
        "etiqueta": "Malware",
        "color": "#7c3aed",
        "palabras": ["malware", "trojan", "troyano", "spyware", "botnet", "gusano", "worm", "backdoor", "puerta trasera", "stealer", "cryptominer", "criptominero", " miner"],
        "svg": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<ellipse cx="12" cy="13" rx="5" ry="6"/>'
            '<path d="M9 8L7 6M15 8l2-2M7 13H3M21 13h-4M9 19l-2 2M15 19l2 2M12 7V4"/>'
            "</svg>"
        ),
    },
    "ddos": {
        "etiqueta": "Ataque DDoS",
        "color": "#be185d",
        "palabras": ["ddos", "denegación de servicio", "denegacion de servicio", "denial of service"],
        "svg": (
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
            'stroke-linecap="round" stroke-linejoin="round">'
            '<circle cx="12" cy="12" r="2.4"/>'
            '<circle cx="4" cy="5" r="1.5"/><circle cx="20" cy="5" r="1.5"/>'
            '<circle cx="4" cy="19" r="1.5"/><circle cx="20" cy="19" r="1.5"/>'
            '<path d="M5.3 6.3L10 10.4M18.7 6.3L14 10.4M5.3 17.7L10 13.6M18.7 17.7L14 13.6"/>'
            "</svg>"
        ),
    },
    # Categoría de la sección "Protección de Datos" (site/proteccion-datos/) --
    # a propósito con "palabras": [] y SIN entrar en ORDEN_CATEGORIAS: nunca se
    # asigna por coincidencia de palabras clave en una noticia de ciberseguridad
    # (evitaría falsos positivos, p.ej. un artículo que solo MENCIONA "datos
    # personales" de paso). Solo se asigna cuando el ítem ya trae
    # item["categoria"] == "proteccion_datos" fijado por feeds.yaml (ver
    # categorizar() más abajo) -- es decir, únicamente para las fuentes de esa
    # sección (dpoec.com, CorralRosales, SPDP).
    "proteccion_datos": {
        "etiqueta": "Protección de Datos",
        "color": "#0e7490",
        "palabras": [],
    },
}

GENERICO = {
    "etiqueta": "Ciberseguridad",
    "color": "#475569",
    "svg": (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 3l7 3v6c0 5-3.5 7.5-7 9-3.5-1.5-7-4-7-9V6l7-3z"/>'
        '<path d="M9 12l2 2 4-4"/>'
        "</svg>"
    ),
}

# Orden de prioridad al buscar coincidencias (las más específicas primero)
ORDEN_CATEGORIAS = ["ransomware", "phishing", "filtracion_datos", "vulnerabilidad", "malware", "ddos"]


def categorizar(item: dict) -> str:
    """Clasifica la noticia. Si el ítem ya trae una categoría FIJA (ver
    feeds.yaml: campo "categoria", usado por fuentes de un solo tema como
    la sección de Protección de Datos), se respeta tal cual -- nunca se
    reemplaza por una inferida de palabras clave. Si no, se clasifica por
    palabras clave en el texto ORIGINAL (sin traducir) del título+extracto,
    para no depender de la calidad de la traducción; si no coincide
    ninguna, usa la categoría genérica de ciberseguridad."""
    categoria_fija = item.get("categoria")
    if categoria_fija in CATEGORIAS:
        return categoria_fija

    texto = f"{item.get('titulo', '')} {item.get('extracto_original', '')}".lower()
    for cat in ORDEN_CATEGORIAS:
        for palabra in CATEGORIAS[cat]["palabras"]:
            if palabra in texto:
                return cat
    return "generico"


def log(mensaje: str) -> None:
    print(f"[build_site] {mensaje}", flush=True)


def cargar_nuevas() -> list[dict]:
    if not NUEVAS_JSON.exists():
        log(f"AVISO: no existe {NUEVAS_JSON}; nada que hacer (¿corriste fetch_news.py antes?).")
        return []
    with open(NUEVAS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def cargar_publicadas() -> dict:
    if not PUBLICADAS_JSON.exists():
        return {"urls": {}}
    try:
        with open(PUBLICADAS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"urls": {}}


def guardar_publicadas(datos: dict) -> None:
    PUBLICADAS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(PUBLICADAS_JSON, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def cargar_recientes_proteccion_datos() -> list[dict]:
    if not PROTECCION_DATOS_JSON.exists():
        return []
    try:
        with open(PROTECCION_DATOS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def guardar_recientes_proteccion_datos(items: list[dict]) -> None:
    PROTECCION_DATOS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(PROTECCION_DATOS_JSON, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def slugificar(texto: str, maximo: int = 60) -> str:
    """Convierte un título en un slug apto para URL: sin acentos, minúsculas,
    solo [a-z0-9-]. Se usa únicamente para que la URL sea legible — la
    identidad real del archivo la da el hash que se le agrega (ver
    nombre_archivo_noticia)."""
    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    solo_alfanumerico = re.sub(r"[^a-z0-9]+", "-", sin_acentos.lower())
    colapsado = re.sub(r"-{2,}", "-", solo_alfanumerico).strip("-")
    return (colapsado[:maximo].rstrip("-")) or "noticia"


def nombre_archivo_noticia(item: dict, fecha_carpeta: str) -> str:
    """AAAA-MM-DD-slug-del-titulo-<hash8>.html — el hash (del enlace original,
    ya verificado por fetch_news.py) garantiza que el archivo sea único aunque
    dos titulares se parezcan; el slug es solo para que la URL sea legible."""
    slug = slugificar(item["titulo"])
    hash_corto = hashlib.sha1(item["enlace"].encode("utf-8")).hexdigest()[:8]
    return f"{fecha_carpeta}-{slug}-{hash_corto}.html"


NOTA_RESUMEN_IA = (
    "Resumen generado con IA (Gemini) a partir del artículo completo — "
    "no es una cita textual; consulta la fuente para el texto exacto."
)
NOTA_RESUMEN_PARCIAL = (
    " Resumen parcial: la fuente no incluye más detalle en su RSS; "
    "lee la noticia completa en la fuente."
)


def preparar_resumen_ampliado(item: dict) -> dict:
    """Para la página de detalle de la noticia: un resumen más completo que
    el de la tarjeta (2-3+ párrafos).

    Fuente del texto, en orden de preferencia:
    1. `resumen_ia`: si resumir_ia.py extrajo el artículo completo (con
       trafilatura) y Gemini lo resumió con éxito (ver ese script), ya viene
       en español y NO se vuelve a traducir — se usa tal cual, solo
       recortado a los mismos topes de siempre por seguridad.
    2. Si no, el extracto/contenido del RSS de siempre (`contenido_ampliado`
       o `extracto_original`), traducido con DeepL. Se recorta a
       LIMITE_RESUMEN_AMPLIADO caracteres y a MAX_PARRAFOS_AMPLIADO
       párrafos — nunca se reproduce el artículo completo, incluso si el
       RSS lo trae entero. Se traduce párrafo por párrafo (no todo el texto
       de una sola vez) para no depender de que DeepL preserve los saltos
       de línea; si CUALQUIER párrafo falla al traducir, se muestran TODOS
       en el idioma original (nunca una mezcla de español e inglés). Si el
       propio extracto de RSS ya venía incompleto (fuente_parece_incompleta),
       se agrega una nota explícita en vez de confiar solo en la elipsis.
    """
    idioma = item.get("idioma", "en")
    resumen_ia = item.get("resumen_ia") if item.get("resumen_ia_ok") else None

    if resumen_ia:
        parrafos_ia = [p.strip() for p in resumen_ia.split("\n") if p.strip()]
        parrafos_ia = acotar_parrafos(parrafos_ia, LIMITE_RESUMEN_AMPLIADO, MAX_PARRAFOS_AMPLIADO)
        parrafos_ia = [rematar_final(p) for p in parrafos_ia]
        return {"parrafos": parrafos_ia, "nota_idioma": NOTA_RESUMEN_IA}

    contenido_original = (item.get("contenido_ampliado") or item.get("extracto_original") or "").strip()

    if not contenido_original:
        return {
            "parrafos": ["El RSS de esta fuente no incluye un extracto más amplio. Consulta el enlace a la fuente al final de esta página para leer el artículo completo."],
            "nota_idioma": "",
        }

    parrafos_originales = [p.strip() for p in contenido_original.split("\n\n") if p.strip()] or [contenido_original]
    parrafos_acotados = acotar_parrafos(parrafos_originales, LIMITE_RESUMEN_AMPLIADO, MAX_PARRAFOS_AMPLIADO)

    # Si el propio párrafo viene incompleto del RSS de origen (o si tuvimos
    # que descartar párrafos posteriores por el tope de caracteres/cantidad),
    # se marca con "…" en vez de dejarlo colgado a media frase; además, si el
    # RSS de origen ya llegaba incompleto (con o sin marcador propio), se
    # agrega una nota explícita en vez de confiar solo en esa elipsis.
    se_recorto_contenido = len(parrafos_acotados) < len(parrafos_originales)
    nota_parcial = NOTA_RESUMEN_PARCIAL if fuente_parece_incompleta(contenido_original) else ""
    parrafos_acotados = [rematar_final(p) for p in parrafos_acotados]
    if se_recorto_contenido and parrafos_acotados and not parrafos_acotados[-1].endswith("…"):
        parrafos_acotados[-1] = parrafos_acotados[-1].rstrip(".!?") + "…"

    if idioma == "es":
        return {"parrafos": parrafos_acotados, "nota_idioma": nota_parcial.strip()}

    if DEEPL_API_KEY:
        parrafos_traducidos = []
        for p in parrafos_acotados:
            t = traducir_deepl(p, idioma)
            if not t:
                parrafos_traducidos = None
                break
            parrafos_traducidos.append(t)
        if parrafos_traducidos:
            nota = "Traducido automáticamente del inglés (DeepL)." if idioma == "en" else f"Traducido automáticamente del {idioma} (DeepL)."
            return {"parrafos": parrafos_traducidos, "nota_idioma": nota + nota_parcial}

    return {
        "parrafos": parrafos_acotados,
        "nota_idioma": "No se pudo traducir automáticamente (se muestra el original)." + nota_parcial,
    }


# ---------------------------------------------------------------------------
# Traducción (DeepL) — nunca inventa: si falla, devuelve None y quien la llama
# decide el fallback seguro (citar el original).
# ---------------------------------------------------------------------------

def _endpoint_deepl(api_key: str) -> str:
    # Las claves del plan gratuito de DeepL terminan en ":fx" y usan un host distinto.
    return "https://api-free.deepl.com/v2/translate" if api_key.endswith(":fx") else "https://api.deepl.com/v2/translate"


def _log_cuerpo_error_deepl(cuerpo_bytes: bytes) -> None:
    """Registra el cuerpo de una respuesta de error de DeepL en el log, para
    diagnóstico — nunca contiene la clave (DeepL no la repite en sus
    respuestas), pero por las dudas se recorta a un tamaño razonable."""
    try:
        texto = cuerpo_bytes.decode("utf-8", errors="replace")
    except Exception:
        texto = "<no se pudo decodificar el cuerpo de la respuesta>"
    log(f"    Cuerpo de la respuesta de DeepL: {texto[:500]}")


def traducir_deepl(texto: str, idioma_origen: str) -> str | None:
    """Traduce `texto` al español usando la API de DeepL. Devuelve None (sin
    lanzar excepción) si no hay clave configurada o si la llamada falla por
    cualquier motivo — nunca se fabrica una traducción alternativa.

    Autenticación: header "Authorization: DeepL-Auth-Key <clave>", que es el
    método que documenta DeepL actualmente (no "Bearer", y no auth_key como
    parámetro del cuerpo). El endpoint se elige según el sufijo de la clave:
    las cuentas API Free terminan en ":fx" y usan api-free.deepl.com; el
    resto (cuentas Pro) usan api.deepl.com.
    """
    if not texto or not DEEPL_API_KEY:
        return None

    datos = urllib.parse.urlencode(
        {
            "text": texto,
            "source_lang": idioma_origen.upper(),
            "target_lang": "ES",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        _endpoint_deepl(DEEPL_API_KEY),
        data=datos,
        method="POST",
        headers={
            "Authorization": f"DeepL-Auth-Key {DEEPL_API_KEY}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=DEEPL_TIMEOUT_SEGUNDOS) as resp:
            if resp.status != 200:
                log(f"  AVISO: DeepL respondió HTTP {resp.status}; se usa el original citado para este ítem.")
                _log_cuerpo_error_deepl(resp.read())
                return None
            cuerpo = json.loads(resp.read().decode("utf-8"))
        traducciones = cuerpo.get("translations") or []
        if not traducciones:
            return None
        return traducciones[0].get("text") or None
    except urllib.error.HTTPError as ex:
        log(f"  AVISO: DeepL HTTPError {ex.code} ({_endpoint_deepl(DEEPL_API_KEY)}); se usa el original citado para este ítem.")
        _log_cuerpo_error_deepl(ex.read())
    except urllib.error.URLError as ex:
        log(f"  AVISO: DeepL URLError ({ex.reason}); se usa el original citado para este ítem.")
    except TimeoutError:
        log("  AVISO: timeout llamando a DeepL; se usa el original citado para este ítem.")
    except (json.JSONDecodeError, KeyError, IndexError) as ex:
        log(f"  AVISO: respuesta inesperada de DeepL ({ex}); se usa el original citado para este ítem.")
    except Exception as ex:  # noqa: BLE001 - una falla de traducción nunca debe tumbar el build
        log(f"  AVISO: error inesperado traduciendo con DeepL ({type(ex).__name__}: {ex}); se usa el original citado.")
    return None


SIN_EXTRACTO_ADICIONAL = "El RSS de la fuente no trae un extracto adicional aparte del titular."


def preparar_texto_mostrado(item: dict) -> dict:
    """Devuelve un dict con los campos ya listos para mostrar, siempre en
    español, sin inventar contenido:
      - titulo_mostrar
      - resumen_mostrar: resumen corto para tarjeta/destacada
      - resumen_meta: para meta descripción/OG/Twitter (~160 car.)
      - nota_idioma: texto corto para la interfaz (o cadena vacía)

    Si resumir_ia.py generó un resumen con IA para este ítem (`resumen_ia` +
    `resumen_ia_ok`), `resumen_mostrar` son las primeras 2-3 oraciones
    COMPLETAS de ese resumen (nunca un corte por cantidad de caracteres a
    media frase) — el título se sigue traduciendo con DeepL igual que
    siempre, resumir_ia.py no lo toca. Si no hay resumen de IA, se usa el
    extracto de RSS de siempre, traducido con DeepL.

    `resumen_mostrar` y `resumen_meta` se calculan cada uno por separado a
    partir del MISMO texto base — nunca se recorta un resumen ya recortado
    (antes la meta descripción se obtenía truncando de nuevo
    `resumen_mostrar`, y ese segundo recorte podía caer en un punto peor que
    el primero)."""
    titulo_original = item["titulo"].strip()
    idioma = item.get("idioma", "en")
    extracto_original = (item.get("extracto_original") or "").strip()
    aporta_info = bool(extracto_original) and extracto_original.lower() != titulo_original.lower() and len(extracto_original) > 15
    resumen_ia = item.get("resumen_ia") if item.get("resumen_ia_ok") else None

    def _resultado(titulo_mostrar: str, texto_base: str | None, nota_idioma: str, resumen_corto: str | None = None) -> dict:
        if texto_base:
            return {
                "titulo_mostrar": titulo_mostrar,
                "resumen_mostrar": resumen_corto if resumen_corto is not None else truncar(texto_base, 600),
                "resumen_meta": truncar(texto_base, 160),
                "nota_idioma": nota_idioma,
            }
        return {
            "titulo_mostrar": titulo_mostrar,
            "resumen_mostrar": SIN_EXTRACTO_ADICIONAL,
            "resumen_meta": SIN_EXTRACTO_ADICIONAL,
            "nota_idioma": nota_idioma,
        }

    # --- Resumen con IA disponible: reemplaza el extracto de RSS como base
    #     del resumen corto (primeras 2-3 oraciones, no un corte por
    #     caracteres). El título se traduce igual que siempre. ---
    if resumen_ia:
        texto_plano = " ".join(linea.strip() for linea in resumen_ia.split("\n") if linea.strip())
        resumen_corto = primeras_oraciones(texto_plano, 3)
        if idioma == "es":
            return _resultado(titulo_original, texto_plano, NOTA_RESUMEN_IA, resumen_corto)
        titulo_traducido = traducir_deepl(titulo_original, idioma)
        if titulo_traducido:
            return _resultado(titulo_traducido, texto_plano, NOTA_RESUMEN_IA, resumen_corto)
        nota = NOTA_RESUMEN_IA + " No se pudo traducir el título automáticamente (se muestra el original)."
        return _resultado(titulo_original, texto_plano, nota, resumen_corto)

    # --- Sin resumen de IA: comportamiento de siempre (extracto de RSS). ---
    if idioma == "es":
        return _resultado(titulo_original, extracto_original if aporta_info else None, "")

    # Fuente en idioma distinto al español: intentar traducir con DeepL.
    if not DEEPL_API_KEY:
        log(f"  AVISO: DEEPL_API_KEY no configurada; '{titulo_original[:60]}...' se muestra citando el original.")

    titulo_traducido = traducir_deepl(titulo_original, idioma)
    extracto_traducido = traducir_deepl(truncar(extracto_original, 700), idioma) if aporta_info else None
    nota_parcial = NOTA_RESUMEN_PARCIAL if (aporta_info and fuente_parece_incompleta(extracto_original)) else ""

    if titulo_traducido:
        nota = "Traducido automáticamente del inglés (DeepL)." if idioma == "en" else f"Traducido automáticamente del {idioma} (DeepL)."
        return _resultado(titulo_traducido, extracto_traducido, nota + nota_parcial)

    # Fallback seguro: no se pudo traducir (sin clave o falló la API). Nunca
    # se inventa una traducción — se muestra el extracto original tal cual,
    # sin repetir el título (ya se muestra arriba, como encabezado de la
    # tarjeta) ni envolverlo en comillas/etiquetas de texto.
    return _resultado(
        titulo_original,
        extracto_original if aporta_info else None,
        "No se pudo traducir automáticamente (se muestra el original)." + nota_parcial,
    )


def fecha_legible(dt: datetime) -> str:
    return f"{dt.day} de {MESES_ES[dt.month - 1]} de {dt.year}"


def fecha_corta(iso_str: str) -> str:
    """Formatea una fecha ISO como día/mes/año, sin nombres de días ni meses
    en inglés (el RSS trae fechas tipo 'Sun, 06 Sep 2026 ...')."""
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    return f"{dt.day:02d}/{dt.month:02d}/{dt.year} · {dt.hour:02d}:{dt.minute:02d} UTC"


def fecha_publicacion_gye(item: dict) -> str:
    """Fecha calendario (Guayaquil) AAAA-MM-DD de la publicación REAL de un
    ítem, a partir de item["fecha_publicacion_iso"] (que fetch_news.py
    siempre guarda en UTC). Se usa para archivar cada noticia bajo su propio
    día real -- no bajo el día en que corrió el workflow que la trajo (ver
    main()). Cadena vacía si el ítem no trae una fecha válida (no debería
    pasar: fetch_news.py descarta cualquier ítem sin fecha)."""
    try:
        fecha_utc = datetime.fromisoformat(item["fecha_publicacion_iso"])
        if fecha_utc.tzinfo is None:
            fecha_utc = fecha_utc.replace(tzinfo=timezone.utc)
        return fecha_utc.astimezone(ZONA_GUAYAQUIL).strftime("%Y-%m-%d")
    except (KeyError, ValueError, TypeError):
        return ""


def render_badge_categoria(categoria: str) -> str:
    info = CATEGORIAS.get(categoria, GENERICO)
    return f'<span class="categoria-badge">{escape(info["etiqueta"])}</span>'


def render_imagen_html(item: dict, categoria: str, titulo_mostrar: str, destacada: bool = False) -> str:
    """Imagen real (ya descargada por fetch_news.py a site/imagenes/…) o, si no
    hay ninguna, el ícono de categoría de respaldo. Ambas llevan siempre la
    insignia de color de la categoría encima.

    El color de la categoría se aplica con una clase CSS (`cat-<categoria>`)
    en el contenedor ancestro (ver render_tarjeta_html/render_pagina_noticia),
    nunca con `style="..."` en línea — así la Content-Security-Policy del
    sitio puede prohibir estilos en línea sin romper nada."""
    ruta_imagen = item.get("imagen_local")
    fuente = escape(item["fuente"])
    badge = render_badge_categoria(categoria)
    clase_extra = " noticia-imagen--destacada" if destacada else ""
    # La imagen destacada/principal (portada, cada archivo del día, y el
    # hero de la propia página de detalle) es la primera imagen de la
    # página -- above the fold. Con loading="lazy" el navegador demora en
    # empezar a pintarla, dejando ver por un momento el fondo del
    # contenedor con el texto "Imagen: X" encima (se veía como si la
    # imagen no llenara el recuadro, pero era nada más una demora de
    # carga, no un problema de CSS/object-fit). Las tarjetas del grid
    # (fuera de la vista inicial) siguen con loading="lazy" -- ahí sí
    # corresponde.
    carga_imagen = 'loading="eager" fetchpriority="high"' if destacada else 'loading="lazy"'

    if ruta_imagen:
        alt = escape(titulo_mostrar, quote=True)
        src = escape(ruta_imagen, quote=True)
        return f"""      <figure class="noticia-imagen{clase_extra}">
        {badge}
        <img src="{src}" alt="{alt}" {carga_imagen} decoding="async">
        <figcaption>Imagen: {fuente}</figcaption>
      </figure>
"""

    info = CATEGORIAS.get(categoria, GENERICO)
    # El ícono es un archivo SVG real en site/assets/iconos/ (no SVG en línea
    # ni solo texto) — un <img> normal, más robusto y fácil de cachear. La
    # transparencia de que es un ícono (no una foto real) se conserva para
    # lectores de pantalla vía alt, y visualmente con una etiqueta pequeña y
    # discreta ("Ilustrativo") en vez de una frase larga en el texto.
    alt_icono = escape(f"Ilustración genérica de la categoría {info['etiqueta']}; no es una foto real del hecho", quote=True)
    return f"""      <figure class="noticia-imagen noticia-imagen--generica{clase_extra}">
        {badge}
        <span class="badge-ilustrativo" title="Esta imagen es un ícono ilustrativo, no una foto real del hecho">Ilustrativo</span>
        <div class="icono-generico-fondo">
          <img class="icono-generico" src="/assets/iconos/{categoria}.svg" alt="{alt_icono}" {carga_imagen} decoding="async">
        </div>
      </figure>
"""


def render_tarjeta_html(item: dict, ruta_noticia: str, es_destacada: bool = False) -> str:
    """Renderiza una noticia como tarjeta de grid (por defecto) o, si
    `es_destacada`, como el bloque grande de "lo más reciente" arriba de la
    portada/edición del día. Todos los enlaces (imagen, título, botón) van a
    la página de detalle propia del sitio (`ruta_noticia`) — nunca directo al
    enlace externo; ese solo aparece al pie de la página de detalle."""
    mostrado = preparar_texto_mostrado(item)
    titulo_mostrar = mostrado["titulo_mostrar"]
    resumen_mostrar = mostrado["resumen_mostrar"]
    nota_idioma = mostrado["nota_idioma"]

    categoria = categorizar(item)
    imagen_html = render_imagen_html(item, categoria, titulo_mostrar, destacada=es_destacada)
    info_categoria = CATEGORIAS.get(categoria, GENERICO)
    eyebrow_categoria = f'<span class="eyebrow-categoria">{escape(info_categoria["etiqueta"])}</span>'

    fuente = escape(item["fuente"])
    enlace_noticia = escape(ruta_noticia, quote=True)
    fecha_str = escape(fecha_corta(item["fecha_publicacion_iso"]))
    resumen_html = escape(resumen_mostrar).replace("\n", "<br>")
    titulo_html = escape(titulo_mostrar)
    nota_html = f'<span class="idioma-nota">{escape(nota_idioma)}</span>' if nota_idioma else ""

    if es_destacada:
        return f"""    <article class="destacada cat-{categoria}">
      <a class="destacada-imagen-enlace" href="{enlace_noticia}">
{imagen_html}      </a>
      <div class="destacada-cuerpo">
        <span class="destacada-eyebrow">Lo más reciente</span>
        {eyebrow_categoria}
        <h2 class="destacada-titulo"><a href="{enlace_noticia}">{titulo_html}</a></h2>
        <p class="destacada-resumen">{resumen_html}</p>
        <div class="noticia-meta">
          <span class="fuente">Fuente: {fuente}</span>
          <span class="fecha">Publicado: {fecha_str}</span>
          {nota_html}
        </div>
        <a class="destacada-cta" href="{enlace_noticia}">Leer la noticia completa →</a>
      </div>
    </article>
"""

    return f"""      <article class="tarjeta cat-{categoria}">
        <a class="tarjeta-imagen-enlace" href="{enlace_noticia}">
{imagen_html}        </a>
        <div class="tarjeta-cuerpo">
          {eyebrow_categoria}
          <h3 class="tarjeta-titulo"><a href="{enlace_noticia}">{titulo_html}</a></h3>
          <p class="tarjeta-resumen">{resumen_html}</p>
          <div class="noticia-meta">
            <span class="fuente">Fuente: {fuente}</span>
            <span class="fecha">Publicado: {fecha_str}</span>
            {nota_html}
          </div>
        </div>
      </article>
"""


# ---------------------------------------------------------------------------
# Botones de "Compartir" (página de detalle): comparten el enlace PROPIO de
# esta página (nunca el enlace externo de la fuente) con el título ya
# traducido. Enlaces estándar de cada red -- sin SDKs, sin claves de API, sin
# scripts de terceros. Íconos SVG en línea, mínimos, monoline.
# ---------------------------------------------------------------------------

_ICONO_WHATSAPP = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M12.01 2C6.5 2 2.02 6.48 2.02 12c0 1.77.46 3.45 1.28 4.9L2 22l5.25-1.27a9.96 9.96 0 0 0 4.76 1.21h.01c5.5 0 9.98-4.48 9.98-10S17.51 2 12.01 2zm0 18.06h-.01a8.4 8.4 0 0 1-4.28-1.17l-.31-.18-3.11.75.75-3.04-.2-.32a8.43 8.43 0 0 1-1.28-4.5c0-4.65 3.79-8.44 8.45-8.44 2.26 0 4.38.88 5.97 2.48a8.38 8.38 0 0 1 2.47 5.97c0 4.65-3.79 8.45-8.45 8.45zm4.63-6.33c-.25-.13-1.5-.74-1.73-.82-.23-.08-.4-.13-.57.13-.17.25-.65.82-.8.99-.15.17-.29.19-.55.06-.25-.13-1.07-.4-2.04-1.27-.75-.67-1.26-1.51-1.41-1.76-.15-.25-.02-.39.11-.51.11-.11.25-.29.37-.44.12-.15.17-.25.25-.42.08-.17.04-.31-.02-.44-.06-.13-.57-1.39-.79-1.9-.21-.5-.42-.43-.57-.44h-.49c-.17 0-.44.06-.67.31-.23.25-.87.86-.87 2.09 0 1.23.9 2.42 1.02 2.59.13.17 1.77 2.79 4.29 3.8.6.26 1.07.42 1.44.53.6.19 1.15.16 1.59.1.48-.07 1.5-.61 1.71-1.21.21-.6.21-1.11.15-1.22-.06-.11-.23-.17-.48-.3z"/></svg>'
_ICONO_X = '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M3 3l7.36 9.64L3.28 21h2.6l6-6.85L16.5 21H21l-7.7-10.1L20.6 3h-2.6l-5.6 6.4L8 3H3zm3.2 1.9h2.2l9.5 12.5h-2.1L6.2 4.9z"/></svg>'
_ICONO_FACEBOOK = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M22 12.06C22 6.5 17.52 2 12 2S2 6.5 2 12.06C2 17.08 5.66 21.24 10.44 22v-7.02H7.9v-2.92h2.54V9.87c0-2.5 1.5-3.89 3.79-3.89 1.1 0 2.24.2 2.24.2v2.46h-1.26c-1.24 0-1.63.77-1.63 1.56v1.86h2.78l-.44 2.92h-2.34V22C18.34 21.24 22 17.08 22 12.06z"/></svg>'
_ICONO_LINKEDIN = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M4.98 3.5A2.5 2.5 0 1 1 5 8.5a2.5 2.5 0 0 1-.02-5zM3.2 9.75h3.6V21H3.2V9.75zM9.5 9.75h3.45v1.54h.05c.48-.9 1.66-1.85 3.42-1.85 3.66 0 4.33 2.4 4.33 5.52V21h-3.6v-5.4c0-1.29-.02-2.94-1.79-2.94-1.8 0-2.07 1.4-2.07 2.85V21H9.5V9.75z"/></svg>'
_ICONO_TELEGRAM = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" aria-hidden="true"><path d="M21.5 3.5L2.9 10.9c-1.2.48-1.2 1.16-.2 1.47l4.8 1.5 1.85 5.66c.23.62.4.87.83.87.35 0 .5-.16.7-.36l1.83-1.78 3.8 2.81c.7.39 1.2.19 1.38-.65l2.5-11.8c.27-1.16-.44-1.68-1.99-1.62zM8.9 14.24l9-5.68c.42-.26.8-.12.49.17l-7.6 6.87-.3 3.24-1.6-4.6z"/></svg>'
_ICONO_COPIAR = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 9h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2z"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>'


def render_botones_compartir(titulo_mostrar: str, ruta_noticia: str) -> str:
    """Enlaces para compartir esta noticia en redes: comparten el enlace de
    ESTA página de detalle (nunca el enlace externo de la fuente), con el
    título ya traducido. Enlaces estándar de cada red (sin SDK, sin clave de
    API); "Copiar enlace" usa navigator.clipboard vía assets/compartir.js
    (ver ese archivo) -- de ahí que script-src ya no sea 'none' (ver
    POLITICA_SEGURIDAD_CONTENIDO)."""
    url = url_absoluta(ruta_noticia)
    url_q = urllib.parse.quote(url, safe="")
    titulo_q = urllib.parse.quote(titulo_mostrar, safe="")
    url_attr = escape(url, quote=True)

    redes = [
        ("WhatsApp", f"https://wa.me/?text={titulo_q}%20{url_q}", _ICONO_WHATSAPP),
        ("X", f"https://twitter.com/intent/tweet?text={titulo_q}&url={url_q}", _ICONO_X),
        ("Facebook", f"https://www.facebook.com/sharer/sharer.php?u={url_q}", _ICONO_FACEBOOK),
        ("LinkedIn", f"https://www.linkedin.com/sharing/share-offsite/?url={url_q}", _ICONO_LINKEDIN),
        ("Telegram", f"https://t.me/share/url?url={url_q}&text={titulo_q}", _ICONO_TELEGRAM),
    ]
    enlaces_html = "\n".join(
        f'          <a class="compartir-boton" href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer" aria-label="Compartir en {nombre}" title="Compartir en {nombre}">{icono}</a>'
        for nombre, href, icono in redes
    )
    return f"""      <div class="compartir">
        <span class="compartir-etiqueta">Compartir:</span>
        <div class="compartir-lista">
{enlaces_html}
          <button type="button" class="compartir-boton compartir-copiar" data-url="{url_attr}" aria-label="Copiar enlace" title="Copiar enlace">
            {_ICONO_COPIAR}
            <span class="compartir-copiar-msg" role="status"></span>
          </button>
        </div>
      </div>
"""


# ---------------------------------------------------------------------------
# Sidebar de la página de detalle: lista de categorías (con el mismo color
# de badge que el resto del sitio) y un bloque corto de "Últimas noticias".
# Usa el espacio que antes quedaba vacío a los costados de la columna de
# lectura en pantallas grandes -- ver .pagina-noticia-layout en style.css --
# y se apila debajo del artículo en pantallas angostas.
# ---------------------------------------------------------------------------

RE_H1_NOTICIA_DETALLE = re.compile(r'<h1 class="noticia-detalle-titulo">(.*?)</h1>', re.DOTALL)


def obtener_items_recientes(excluir_enlace: str | None, limite: int = 5) -> list[dict]:
    """Las `limite` noticias más recientes YA PUBLICADAS (según
    data/publicadas.json, el ledger real), para el bloque "Últimas
    noticias" del sidebar -- nunca incluye la noticia que se está
    mostrando. El título que se muestra es el que YA está en la propia
    página de detalle de cada candidata (su <h1>), no el título crudo del
    ledger -- así se respeta la traducción (o la nota de "no se pudo
    traducir") que esa página ya tiene, sin re-traducir ni inventar nada
    acá."""
    publicadas = cargar_publicadas()
    candidatos = [
        info
        for enlace, info in publicadas.get("urls", {}).items()
        if enlace != excluir_enlace and info.get("ruta_noticia") and info.get("fecha_publicacion_iso")
    ]
    candidatos.sort(key=lambda x: x["fecha_publicacion_iso"], reverse=True)

    resultado: list[dict] = []
    for info in candidatos:
        if len(resultado) >= limite:
            break
        archivo = NOTICIA_DIR / Path(info["ruta_noticia"]).name
        if not archivo.exists():
            continue
        texto = archivo.read_text(encoding="utf-8")
        m_titulo = RE_H1_NOTICIA_DETALLE.search(texto)
        if not m_titulo:
            continue
        titulo_mostrado = unescape(re.sub(r"<[^>]*>", "", m_titulo.group(1))).strip()
        resultado.append(
            {
                "titulo": titulo_mostrado,
                "ruta_noticia": info["ruta_noticia"],
                "fecha_publicacion_iso": info["fecha_publicacion_iso"],
            }
        )
    return resultado


def render_sidebar_noticia(items_recientes: list[dict]) -> str:
    categorias_html = "".join(
        f'          <li><a class="sidebar-categoria-enlace cat-{slug}" href="/archivo/index.html">{escape(CATEGORIAS[slug]["etiqueta"])}</a></li>\n'
        for slug in ORDEN_CATEGORIAS
    )

    if items_recientes:
        recientes_html = "".join(
            f"""          <li>
            <a href="{escape(it['ruta_noticia'], quote=True)}">{escape(it['titulo'])}</a>
            <span class="sidebar-recientes-fecha">{escape(fecha_corta(it['fecha_publicacion_iso']))}</span>
          </li>
"""
            for it in items_recientes
        )
        bloque_recientes = f"""      <section class="sidebar-bloque">
        <h2 class="sidebar-titulo">Últimas noticias</h2>
        <ul class="sidebar-recientes">
{recientes_html}        </ul>
      </section>
"""
    else:
        bloque_recientes = ""

    return f"""    <aside class="sidebar-noticia">
      <section class="sidebar-bloque">
        <h2 class="sidebar-titulo">Categorías</h2>
        <ul class="sidebar-categorias">
{categorias_html}        </ul>
      </section>
{bloque_recientes}    </aside>
"""


def render_fuentes_adicionales(fuentes_adicionales: list[dict] | None) -> str:
    """Cuando fetch_news.py detectó que más de una fuente cubrió el MISMO
    hecho (ver fusionar_mismo_hecho() ahí), item["fuentes_adicionales"]
    trae el nombre real y el enlace real de cada fuente adicional -- nunca
    inventado. Se muestra como "También cubierto por: X, Y" con cada
    nombre como hipervínculo directo a esa fuente."""
    if not fuentes_adicionales:
        return ""
    enlaces_html = ", ".join(
        f'<a href="{escape(f["enlace"], quote=True)}" target="_blank" rel="noopener noreferrer">{escape(f["nombre"])}</a>'
        for f in fuentes_adicionales
    )
    return f'      <p class="fuentes-adicionales">También cubierto por: {enlaces_html}</p>\n'


def render_pagina_noticia(item: dict, ruta_noticia: str) -> str:
    """Página de detalle propia del sitio para una noticia: resumen ampliado
    en español (parafraseado a partir del texto más completo del RSS, nunca
    el artículo completo — ver preparar_resumen_ampliado), imagen/ícono y
    fuente visibles, y al final el enlace al artículo original (única salida
    externa del sitio para esta noticia)."""
    mostrado = preparar_texto_mostrado(item)
    titulo_mostrar = mostrado["titulo_mostrar"]
    nota_idioma_titulo = mostrado["nota_idioma"]

    ampliado = preparar_resumen_ampliado(item)
    nota_idioma_ampliado = ampliado["nota_idioma"]

    categoria = categorizar(item)
    info_categoria = CATEGORIAS.get(categoria, GENERICO)
    imagen_html = render_imagen_html(item, categoria, titulo_mostrar, destacada=True)

    fuente = escape(item["fuente"])
    enlace_externo = escape(item["enlace"], quote=True)
    fecha_str = escape(fecha_corta(item["fecha_publicacion_iso"]))
    titulo_html = escape(titulo_mostrar)
    descripcion = mostrado["resumen_meta"]
    # El og:image de la página de detalle debe ser SIEMPRE la misma imagen
    # que ya se ve en esa página (imagen_html arriba): la imagen real del
    # artículo (o el logo de la SPDP, que también vive en imagen_local para
    # sus boletines) si existe, o si no, el ícono de categoría -- nunca el
    # logo de la empresa, que quedaba usándose por error como respaldo acá
    # (bug: WhatsApp/redes mostraban el logo de DERENZIN en vez del ícono de
    # categoría en cualquier noticia sin imagen real). El logo de la empresa
    # sigue siendo el respaldo correcto para la portada general del sitio
    # (index.html) y para ediciones sin ninguna noticia con imagen, pero no
    # para una página de detalle individual, que siempre tiene al menos un
    # ícono de categoría que mostrar.
    #
    # Nota: NO se usa el .svg de /assets/iconos/ acá -- WhatsApp/Facebook no
    # renderizan SVG como og:image (su crawler solo soporta formatos
    # rasterizados), así que un og:image en SVG hubiera mostrado la vista
    # previa en blanco en vez de arreglar nada. /assets/og/{categoria}.png es
    # una versión rasterizada de cada ícono (mismo dibujo, en blanco, sobre
    # un fondo sólido del color de la categoría) generada una sola vez para
    # este propósito -- ver /assets/og/README.md.
    imagen_pagina = item.get("imagen_local") or f"/assets/og/{categoria}.png"
    # Solo para la vista previa de redes sociales (og:description/
    # twitter:description) -- NO para el <meta name="description"> que usan
    # los buscadores. No toca el "Fuente: {fuente real}" que ya se muestra
    # visiblemente en el cuerpo de la página (ese sigue apuntando a la
    # fuente original de siempre); esto es aparte, para que quien reciba el
    # link en WhatsApp/Facebook/Telegram vea de entrada que viene de este sitio.
    descripcion_social = f"Fuente: noticias.derenzin.com — {descripcion}"

    parrafos_html = "\n".join(
        f"        <p>{escape(p)}</p>" for p in ampliado["parrafos"]
    )

    # Puede haber dos notas de idioma distintas: la del título (tarjeta) y la
    # del resumen ampliado (traducciones independientes, cada una con su
    # propio intento). Si coinciden en texto, se muestra una sola vez.
    notas = [n for n in {nota_idioma_titulo, nota_idioma_ampliado} if n]
    notas_html = "".join(f'<span class="idioma-nota">{escape(n)}</span>' for n in notas)

    items_recientes = obtener_items_recientes(item.get("enlace"))
    sidebar_html = render_sidebar_noticia(items_recientes)
    fuentes_adicionales_html = render_fuentes_adicionales(item.get("fuentes_adicionales"))

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="{POLITICA_SEGURIDAD_CONTENIDO}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{titulo_html} — Noticias de Ciberseguridad</title>
{render_meta_seo(f"{titulo_mostrar} — Noticias de Ciberseguridad", descripcion, ruta_noticia, imagen_pagina, tipo_og="article", descripcion_social=descripcion_social)}
  <link rel="icon" href="../assets/favicon.png">
  <meta name="theme-color" content="#00b8d4">
{ENLACES_FUENTE}
  <link rel="stylesheet" href="../style.css">
{render_json_ld_noticia(item, titulo_mostrar, ruta_noticia, categoria)}
</head>
<body>
{render_cabecera("Detalle de la noticia", "../", "noticia")}
  <main class="contenido pagina-noticia">
    <div class="pagina-noticia-layout">
    <article class="noticia-detalle cat-{categoria}">
      <span class="eyebrow-categoria">{escape(info_categoria["etiqueta"])}</span>
      <h1 class="noticia-detalle-titulo">{titulo_html}</h1>
      <div class="noticia-meta">
        <span class="fuente">Fuente: <a href="{enlace_externo}" target="_blank" rel="noopener noreferrer">{fuente}</a></span>
        <span class="fecha">Publicado: {fecha_str}</span>
        {notas_html}
      </div>
{fuentes_adicionales_html}{render_botones_compartir(titulo_mostrar, ruta_noticia)}{imagen_html}      <div class="noticia-detalle-cuerpo">
{parrafos_html}
      </div>
    </article>
{sidebar_html}    </div>
  </main>

{render_pie("../")}
  <script src="../assets/compartir.js" defer></script>
</body>
</html>
"""


FUENTES_MONITOREADAS = "The Hacker News, BleepingComputer, Krebs on Security, Dark Reading, WeLiveSecurity (ESET), INCIBE-CERT."


def render_cabecera(subtitulo: str, prefijo: str, pagina_actual: str) -> str:
    """`prefijo`: '' en site/index.html, '../' en cualquier página un nivel
    adentro (site/archivo/*.html, site/noticia/*.html,
    site/proteccion-datos/index.html). `pagina_actual`: 'portada', 'archivo',
    'noticia' o 'proteccion_datos', para resaltar el link activo.

    Los links a "Archivo" y "Protección de Datos" usan ruta ABSOLUTA desde
    la raíz del sitio (no relativa a `prefijo`), porque apuntan a un
    directorio HERMANO del actual, no al padre -- con solo `prefijo` no hay
    forma de distinguir "estoy en site/archivo/" de "estoy en
    site/noticia/" (ambos tienen prefijo="../"). Antes de este cambio,
    "Archivo" resolvía mal desde site/noticia/*.html (apuntaba a
    site/noticia/index.html, que no existe -> 404)."""
    nav_portada_clase = ' class="activo"' if pagina_actual == "portada" else ""
    nav_archivo_clase = ' class="activo"' if pagina_actual == "archivo" else ""
    nav_proteccion_clase = ' class="activo"' if pagina_actual == "proteccion_datos" else ""
    return f"""  <header class="cabecera">
    <div class="cabecera-contenido">
      <div class="marca">
        <a href="{prefijo}index.html" class="marca-enlace">
          <img src="{prefijo}assets/logo-derenzin.png" alt="DERENZIN" class="marca-logo">
          <div class="marca-texto">
            <span class="marca-titulo">Noticias de Ciberseguridad</span>
            <span class="marca-byline">Un proyecto de <strong>DERENZIN S.A.S.</strong></span>
          </div>
        </a>
        <a href="https://derenzin.com" target="_blank" rel="noopener noreferrer" class="enlace-derenzin">derenzin.com ↗</a>
      </div>
      <p class="subtitulo">{escape(subtitulo)}</p>
      <nav class="nav">
        <a href="{prefijo}index.html"{nav_portada_clase}>Inicio</a>
        <a href="/archivo/index.html"{nav_archivo_clase}>Archivo</a>
        <a href="/proteccion-datos/index.html"{nav_proteccion_clase}>Protección de Datos</a>
      </nav>
    </div>
  </header>
"""


# Google Fonts (Inter) — la misma fuente en las tres plantillas de página.
ENLACES_FUENTE = """  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">"""


def render_pie(prefijo: str) -> str:
    anio = datetime.now(ZONA_GUAYAQUIL).year
    return f"""  <footer class="pie">
    <div class="pie-contenido">
      <div class="pie-marca">
        <a href="https://derenzin.com" target="_blank" rel="noopener noreferrer" class="pie-marca-enlace">
          <img src="{prefijo}assets/logo-derenzin.png" alt="DERENZIN" class="pie-logo">
          <span class="pie-marca-nombre">DERENZIN S.A.S.</span>
        </a>
        <p class="pie-tagline">Un proyecto de DERENZIN S.A.S. — <a href="https://derenzin.com" target="_blank" rel="noopener noreferrer">derenzin.com ↗</a></p>
      </div>
      <div class="pie-legal">
        <p>{escape(AVISO_LEGAL)}</p>
        <p class="pie-fuentes">Fuentes monitoreadas: {FUENTES_MONITOREADAS}</p>
        <p class="pie-recurso">¿Tu organización necesita cumplir con la LOPDP? <a href="https://lopdp.derenzin.com" target="_blank" rel="noopener noreferrer">Metodología de cumplimiento LOPDP de DERENZIN ↗</a></p>
        <p class="pie-copyright">© {anio} DERENZIN S.A.S. — Feddor Derenzin Martínez. Todos los derechos reservados.</p>
      </div>
    </div>
  </footer>
"""


def render_pagina_index(titulo_pagina: str, subtitulo: str, items_html: str, descripcion: str, imagen_og: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="{POLITICA_SEGURIDAD_CONTENIDO}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(titulo_pagina)}</title>
{render_meta_seo(titulo_pagina, descripcion, "", imagen_og)}
  <link rel="icon" href="assets/favicon.png">
  <meta name="theme-color" content="#00b8d4">
{ENLACES_FUENTE}
  <link rel="stylesheet" href="style.css">
</head>
<body>
{render_cabecera(subtitulo, "", "portada")}
  <main class="contenido portada">
    <h1 class="sr-only">{escape(titulo_pagina)}</h1>
{items_html}
  </main>

{render_pie("")}
</body>
</html>
"""


def render_pagina_archivo_dia(fecha_str: str, subtitulo: str, items_html: str, descripcion: str, imagen_og: str) -> str:
    titulo_pagina = f"Ciberseguridad — edición del {fecha_str}"
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="{POLITICA_SEGURIDAD_CONTENIDO}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(titulo_pagina)}</title>
{render_meta_seo(titulo_pagina, descripcion, f"archivo/{fecha_str}.html", imagen_og)}
  <link rel="icon" href="../assets/favicon.png">
  <meta name="theme-color" content="#00b8d4">
{ENLACES_FUENTE}
  <link rel="stylesheet" href="../style.css">
</head>
<body>
{render_cabecera(subtitulo, "../", "archivo")}
  <main class="contenido archivo-dia">
    <h1 class="sr-only">{escape(titulo_pagina)}</h1>
{items_html}
  </main>

{render_pie("../")}
</body>
</html>
"""


# La imagen destacada real de cada día se lee de la propia edición ya
# publicada (site/archivo/AAAA-MM-DD.html) -- mismo patrón que el resto del
# sitio usa para no depender de datos que ya se sobreescribieron: nunca
# inventa una imagen, cae al logo si esa edición no tiene ninguna foto real
# (igual criterio que el og:image de esa misma página).
RE_IMG_DESTACADA_ARCHIVO = re.compile(
    r'noticia-imagen--destacada">\s*<span class="categoria-badge">[^<]*</span>\s*<img src="(/imagenes/[^"]+)"'
)
RE_HREF_NOTICIA_ARCHIVO = re.compile(r'href="(/noticia/[^"]+)"')


def _info_dia_archivo(fecha: str) -> dict:
    ruta = ARCHIVO_DIR / f"{fecha}.html"
    if not ruta.exists():
        return {"imagen": "/assets/logo-derenzin.png", "cantidad": 0}
    texto = ruta.read_text(encoding="utf-8")
    m_img = RE_IMG_DESTACADA_ARCHIVO.search(texto)
    imagen = m_img.group(1) if m_img else "/assets/logo-derenzin.png"
    cantidad = len(set(RE_HREF_NOTICIA_ARCHIVO.findall(texto)))
    return {"imagen": imagen, "cantidad": cantidad}


def _render_tarjeta_dia(fecha: str, imagen: str, cantidad: int) -> str:
    """Tarjeta de una edición diaria para archivo/index.html -- mismas
    clases (.tarjeta/.noticia-imagen/.noticia-meta) que el resto del sitio,
    para que se vea como parte del mismo sistema, no una sección aparte."""
    fecha_dt = datetime.strptime(fecha, "%Y-%m-%d").replace(hour=12, tzinfo=ZONA_GUAYAQUIL)
    texto_fecha = fecha_legible(fecha_dt)
    alt = escape(f"Edición del {texto_fecha}", quote=True)
    plural = "noticia" if cantidad == 1 else "noticias"
    return f"""      <article class="tarjeta">
        <a class="tarjeta-imagen-enlace" href="{fecha}.html">
          <figure class="noticia-imagen">
            <img src="{escape(imagen, quote=True)}" alt="{alt}" loading="lazy" decoding="async">
          </figure>
        </a>
        <div class="tarjeta-cuerpo">
          <h2 class="tarjeta-titulo"><a href="{fecha}.html">{escape(texto_fecha)}</a></h2>
          <div class="noticia-meta">
            <span class="fecha">{cantidad} {plural}</span>
          </div>
        </div>
      </article>
"""


def render_archivo_index(dias: list[str]) -> str:
    if dias:
        tarjetas_html = "".join(
            _render_tarjeta_dia(d, **_info_dia_archivo(d)) for d in sorted(dias, reverse=True)
        )
        lista = f"""<div class="grid-noticias">
{tarjetas_html}    </div>"""
    else:
        lista = '<p class="sin-noticias">Todavía no hay ediciones archivadas.</p>'

    titulo_pagina = "Archivo — Noticias de Ciberseguridad"
    descripcion = "Índice de todas las ediciones diarias publicadas del Noticias de Ciberseguridad — un proyecto de DERENZIN S.A.S."
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="{POLITICA_SEGURIDAD_CONTENIDO}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(titulo_pagina)}</title>
{render_meta_seo(titulo_pagina, descripcion, "archivo/index.html", "/assets/logo-derenzin.png")}
  <link rel="icon" href="../assets/favicon.png">
  <meta name="theme-color" content="#00b8d4">
{ENLACES_FUENTE}
  <link rel="stylesheet" href="../style.css">
</head>
<body>
{render_cabecera("Archivo de ediciones anteriores", "../", "archivo")}
  <main class="contenido">
    <h1 class="sr-only">{escape(titulo_pagina)}</h1>
    {lista}
  </main>

{render_pie("../")}
</body>
</html>
"""


BLOQUE_FUENTE_OFICIAL_SPDP = """    <aside class="fuente-oficial">
      <h2 class="fuente-oficial-titulo">Fuente oficial</h2>
      <p class="fuente-oficial-nombre">Superintendencia de Protección de Datos Personales (SPDP)</p>
      <p class="fuente-oficial-texto">Ente rector de la Ley Orgánica de Protección de Datos Personales (LOPDP) en Ecuador. Consulta directamente sus boletines y resoluciones oficiales.</p>
      <a class="fuente-oficial-cta" href="https://spdp.gob.ec/prensa/" target="_blank" rel="noopener noreferrer">Ver boletines de prensa de la SPDP ↗</a>
    </aside>
"""

# Enlace de referencia a un recurso propio de DERENZIN (no es una fuente de
# noticias -- no se valida como RSS ni se procesa como ítem, es solo un
# enlace fijo, igual de visible que el bloque de la SPDP pero claramente
# distinguido como "recurso propio" en vez de "fuente oficial").
BLOQUE_RECURSO_PROPIO_LOPDP = """    <aside class="recurso-propio">
      <h2 class="recurso-propio-titulo">Recurso propio</h2>
      <p class="recurso-propio-texto">¿Tu organización necesita cumplir con la LOPDP? Conoce la metodología de cumplimiento de DERENZIN S.A.S.</p>
      <a class="recurso-propio-cta" href="https://lopdp.derenzin.com" target="_blank" rel="noopener noreferrer">Más sobre cumplimiento LOPDP: Metodología DERENZIN ↗</a>
    </aside>
"""


def render_pagina_seccion_proteccion_datos(items_html: str, descripcion: str, imagen_og: str) -> str:
    """Portada propia de la sección "Protección de Datos" (site/proteccion-datos/index.html).

    A diferencia de site/index.html (que solo refleja la última edición del
    día de ciberseguridad), esta página se reconstruye cada vez que corre
    build_site.py a partir de data/proteccion_datos_recientes.json -- una
    lista propia de los últimos ítems de esta sección, independiente del
    ciclo diario -- para que siempre muestre contenido reciente aunque hoy
    no haya habido ninguna noticia nueva de esta sección en particular.

    Siempre incluye, además de las noticias, dos bloques fijos: "Fuente
    oficial" con el enlace directo a la Superintendencia de Protección de
    Datos Personales (SPDP) -- el ente rector de la LOPDP en Ecuador,
    cuyos boletines de prensa se traen vía scripts/fetch_spdp.py -- y
    "Recurso propio" con un enlace a la metodología de cumplimiento LOPDP
    de DERENZIN (lopdp.derenzin.com) -- un recurso propio, claramente
    distinguido del anterior, no una fuente de noticias."""
    titulo_pagina = "Protección de Datos — Noticias de Ciberseguridad"
    subtitulo = "Sección de Protección de Datos"
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="{POLITICA_SEGURIDAD_CONTENIDO}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(titulo_pagina)}</title>
{render_meta_seo(titulo_pagina, descripcion, "proteccion-datos/index.html", imagen_og)}
  <link rel="icon" href="../assets/favicon.png">
  <meta name="theme-color" content="#00b8d4">
{ENLACES_FUENTE}
  <link rel="stylesheet" href="../style.css">
</head>
<body>
{render_cabecera(subtitulo, "../", "proteccion_datos")}
  <main class="contenido portada">
    <h1 class="sr-only">{escape(titulo_pagina)}</h1>
{BLOQUE_FUENTE_OFICIAL_SPDP}{BLOQUE_RECURSO_PROPIO_LOPDP}{items_html}
  </main>

{render_pie("../")}
</body>
</html>
"""


def generar_sitemap() -> None:
    """Escanea site/ (no solo lo publicado hoy) y regenera sitemap.xml con
    todas las páginas: portada, índice de archivo, cada día archivado y cada
    noticia. Se corre al final de cada publicación real."""
    ahora_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entradas: list[tuple[str, str]] = []

    def agregar(ruta_relativa: str, archivo: Path) -> None:
        try:
            lastmod = datetime.fromtimestamp(archivo.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        except OSError:
            lastmod = ahora_str
        entradas.append((url_absoluta(ruta_relativa), lastmod))

    if (SITE_DIR / "index.html").exists():
        agregar("", SITE_DIR / "index.html")
    if (ARCHIVO_DIR / "index.html").exists():
        agregar("archivo/index.html", ARCHIVO_DIR / "index.html")
    for p in sorted(ARCHIVO_DIR.glob("*.html")):
        if p.stem != "index":
            agregar(f"archivo/{p.name}", p)
    if (PROTECCION_DATOS_DIR / "index.html").exists():
        agregar("proteccion-datos/index.html", PROTECCION_DATOS_DIR / "index.html")
    for p in sorted(NOTICIA_DIR.glob("*.html")):
        agregar(f"noticia/{p.name}", p)

    urls_xml = "\n".join(
        f"  <url>\n    <loc>{escape(loc, quote=True)}</loc>\n    <lastmod>{lastmod}</lastmod>\n  </url>"
        for loc, lastmod in entradas
    )
    contenido = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}
</urlset>
"""
    with open(SITE_DIR / "sitemap.xml", "w", encoding="utf-8") as f:
        f.write(contenido)
    log(f"Escrito {SITE_DIR / 'sitemap.xml'} ({len(entradas)} URL(s)).")


def _es_proteccion_datos(item: dict) -> bool:
    return item.get("categoria") == "proteccion_datos"


def _renderizar_grid(items: list[dict], rutas_noticia: dict[str, str]) -> str:
    """Arma el HTML de una destacada + cuadrícula a partir de una lista de
    ítems (ya ordenada, más reciente primero) -- usado tanto para la
    edición de ciberseguridad del día como para la sección de Protección de
    Datos. Devuelve cadena vacía si `items` está vacía (quien llama decide
    qué mostrar en ese caso)."""
    if not items:
        return ""
    destacada_html = render_tarjeta_html(items[0], rutas_noticia[items[0]["enlace"]], es_destacada=True)
    tarjetas_html = "".join(
        render_tarjeta_html(item, rutas_noticia[item["enlace"]], es_destacada=False) for item in items[1:]
    )
    titulo_seccion = '    <h2 class="seccion-titulo">Últimas noticias</h2>\n' if items[1:] else ""
    # El marcador FIN-GRID va DENTRO de .grid-noticias, justo antes de su
    # </div> de cierre -- antes quedaba DESPUÉS de ese </div> (fuera de la
    # cuadrícula), así que cuando main() anexaba tarjetas nuevas "antes del
    # marcador" en una segunda corrida del mismo día, esas tarjetas nuevas
    # quedaban como hermanas de .grid-noticias en vez de hijas: no reciben
    # el ancho de columna de la cuadrícula (grid-template-columns), así que
    # ocupan el ancho completo del contenedor y, por el aspect-ratio de la
    # imagen, se ven mucho más altas que las demás -- confirmado en vivo:
    # 4 tarjetas anexadas en corridas posteriores medían 640px de imagen
    # (el ancho completo) contra 203px (el ancho de columna) de las demás.
    return (
        destacada_html
        + titulo_seccion
        + f"""
    <div class="grid-noticias">
{tarjetas_html}    <!-- FIN-GRID -->
    </div>
"""
    )


# ---------------------------------------------------------------------------
# Reparación/blindaje de etiquetas de categoría (categoria-badge + eyebrow-
# categoria): ambas SIEMPRE deben mostrar la misma categoría que la propia
# noticia -- ver render_tarjeta_html()/render_pagina_noticia(), que ya las
# arman a partir de una única variable `categoria` compartida, así que un
# build normal nunca las desincroniza. El desajuste real viene de scripts de
# backfill puntuales (p.ej. backfill_imagenes.py) que reescriben SOLO el
# bloque de imagen (con su categoria-badge) de una tarjeta ya publicada --
# usando la categoría que le corresponde a esa noticia en particular --, sin
# tocar el <span class="eyebrow-categoria"> vecino, que queda con el valor
# de cuando se publicó por primera vez. La clase `cat-<categoria>` del propio
# <article> nunca se toca por separado (sale del mismo `categoria` que la
# figura y el eyebrow en el momento del build), así que es la fuente de
# verdad más confiable para reparar cualquier desajuste después del hecho.
RE_ARTICLE_CATEGORIA = re.compile(
    r'(<article class="(?:destacada|tarjeta|noticia-detalle) cat-([a-z_]+)">)(.*?)(\n\s*</article>)',
    re.DOTALL,
)
RE_BADGE_SPAN = re.compile(r'<span class="categoria-badge">[^<]*</span>')
RE_EYEBROW_SPAN = re.compile(r'<span class="eyebrow-categoria">[^<]*</span>')


def sincronizar_badges_categoria(texto: str) -> tuple[str, int]:
    """Recorre cada <article class="... cat-X"> de `texto` y fuerza que su
    categoria-badge y su eyebrow-categoria muestren la etiqueta de X (la
    categoría real de esa noticia, según la propia clase del <article>).
    Devuelve el texto corregido y cuántas etiquetas se corrigieron (0 si ya
    estaba todo consistente -- caso normal en un build recién hecho)."""
    corregidas = 0

    def _reparar_bloque(m: re.Match) -> str:
        nonlocal corregidas
        apertura, categoria, cuerpo, cierre = m.groups()
        etiqueta_correcta = escape(CATEGORIAS.get(categoria, GENERICO)["etiqueta"])
        badge_correcto = f'<span class="categoria-badge">{etiqueta_correcta}</span>'
        eyebrow_correcto = f'<span class="eyebrow-categoria">{etiqueta_correcta}</span>'

        def _contar_y_reemplazar(patron: re.Pattern, reemplazo: str, texto_local: str) -> str:
            nonlocal corregidas
            nuevo, n = patron.subn(reemplazo, texto_local, count=1)
            if n and nuevo != texto_local:
                corregidas += 1
            return nuevo

        cuerpo = _contar_y_reemplazar(RE_BADGE_SPAN, badge_correcto, cuerpo)
        cuerpo = _contar_y_reemplazar(RE_EYEBROW_SPAN, eyebrow_correcto, cuerpo)
        return apertura + cuerpo + cierre

    texto_corregido = RE_ARTICLE_CATEGORIA.sub(_reparar_bloque, texto)
    return texto_corregido, corregidas


def reparar_badges_categoria_en_sitio() -> int:
    """Aplica sincronizar_badges_categoria() a todo el HTML ya publicado
    (portada, cada edición de archivo, Protección de Datos y cada página de
    detalle), reescribiendo solo los archivos que de verdad cambian. Se
    corre al final de cada build (blindaje permanente) y también puede
    invocarse sola con `--reparar-badges` para corregir el sitio ya
    publicado sin necesitar noticias nuevas."""
    archivos = []
    if (SITE_DIR / "index.html").exists():
        archivos.append(SITE_DIR / "index.html")
    archivos += sorted(p for p in ARCHIVO_DIR.glob("*.html") if p.stem != "index")
    if (PROTECCION_DATOS_DIR / "index.html").exists():
        archivos.append(PROTECCION_DATOS_DIR / "index.html")
    archivos += sorted(NOTICIA_DIR.glob("*.html"))

    total_corregidas = 0
    archivos_tocados = 0
    for archivo in archivos:
        texto = archivo.read_text(encoding="utf-8")
        texto_corregido, corregidas = sincronizar_badges_categoria(texto)
        if corregidas:
            archivo.write_text(texto_corregido, encoding="utf-8")
            archivos_tocados += 1
            total_corregidas += corregidas
            log(f"  Corregidas {corregidas} etiqueta(s) de categoría en {archivo.relative_to(RAIZ)}.")
    log(f"Blindaje de categoría: {total_corregidas} etiqueta(s) corregida(s) en {archivos_tocados} archivo(s).")
    return total_corregidas


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera el sitio a partir de data/nuevas_hoy.json.")
    parser.add_argument(
        "--fecha",
        type=str,
        default=None,
        help=(
            "AAAA-MM-DD: fuerza la fecha de la edición/archivo del día, en vez de la fecha "
            "real actual -- para backfills puntuales que publican noticias con su fecha real "
            "de publicación (ver scripts/backfill_septiembre.py). El workflow diario normal "
            "no pasa este argumento, así que su comportamiento no cambia."
        ),
    )
    parser.add_argument(
        "--reparar-badges",
        action="store_true",
        help=(
            "No genera nada nuevo: solo recorre el sitio ya publicado (portada, archivo, "
            "Protección de Datos y páginas de detalle) y corrige cualquier categoria-badge/"
            "eyebrow-categoria desincronizado de su propia noticia -- ver "
            "sincronizar_badges_categoria(). Útil para reparar el sitio sin necesitar "
            "noticias nuevas ni claves de API."
        ),
    )
    args = parser.parse_args()

    if args.reparar_badges:
        reparar_badges_categoria_en_sitio()
        return

    nuevos = cargar_nuevas()

    if not nuevos:
        log("Sin novedades hoy: no se genera ninguna página nueva ni se toca el ledger.")
        return

    if not DEEPL_API_KEY:
        log("AVISO GENERAL: DEEPL_API_KEY no está configurada. Las noticias en inglés se publicarán citando el título/extracto original (sin traducir) en vez de fallar o inventar una traducción.")

    if args.fecha:
        # Mediodía Guayaquil: no hay una "hora real de publicación de la
        # edición" para un backfill, mediodía es una convención neutra (igual
        # que en fetch_spdp.py para boletines sin hora exacta). fecha_legible()
        # y fecha_str solo usan la parte de fecha, así que la hora no afecta
        # nada visible.
        ahora_gye = datetime.strptime(args.fecha, "%Y-%m-%d").replace(hour=12, tzinfo=ZONA_GUAYAQUIL)
    else:
        ahora_gye = datetime.now(ZONA_GUAYAQUIL)
    fecha_str = ahora_gye.strftime("%Y-%m-%d")
    # Nota: a propósito NO se muestra el conteo de ítems en el HTML — ese
    # número es información de diagnóstico del proceso, no algo para el
    # lector; el conteo real ya queda en el log del workflow de GitHub Actions.
    # (El subtítulo "Edición del ..." ya no se arma acá con fecha_str: cada
    # archivo/edición del día usa el subtítulo de SU PROPIA fecha real de
    # publicación -- ver el agrupamiento por fecha_publicacion_gye() más
    # abajo.)

    # Cada noticia tiene su propia página de detalle dentro del sitio
    # (site/noticia/AAAA-MM-DD-slug-hash.html); todos los enlaces de portada,
    # cuadrícula y archivo apuntan ahí — no directo a la fuente externa (esa
    # solo aparece al pie de la página de detalle). Esto aplica por igual a
    # las dos secciones del sitio (ciberseguridad y Protección de Datos) --
    # ambas comparten la misma plantilla/carpeta de detalle.
    NOTICIA_DIR.mkdir(parents=True, exist_ok=True)
    rutas_noticia: dict[str, str] = {}
    for item in nuevos:
        nombre_archivo = nombre_archivo_noticia(item, fecha_str)
        rutas_noticia[item["enlace"]] = f"/noticia/{nombre_archivo}"
        pagina_noticia = render_pagina_noticia(item, rutas_noticia[item["enlace"]])
        with open(NOTICIA_DIR / nombre_archivo, "w", encoding="utf-8") as f:
            f.write(pagina_noticia)
    log(f"Generadas {len(rutas_noticia)} página(s) de detalle en {NOTICIA_DIR}.")

    # A partir de acá, cada sección se procesa por separado: la portada y el
    # archivo de ciberseguridad NUNCA mezclan noticias de Protección de
    # Datos (y viceversa) -- son dos secciones del mismo sitio, no una lista
    # única.
    nuevos_ciber = [item for item in nuevos if not _es_proteccion_datos(item)]
    nuevos_proteccion = [item for item in nuevos if _es_proteccion_datos(item)]

    SITE_DIR.mkdir(parents=True, exist_ok=True)

    if nuevos_ciber:
        # Cada noticia se archiva bajo la fecha calendario (Guayaquil) de su
        # publicación REAL (fecha_publicacion_gye), NO bajo fecha_str (el
        # día en que corrió este build) -- si no, un ítem publicado ayer
        # pero recién detectado hoy (p.ej. llegó tarde a un feed) queda
        # amontonado bajo "hoy" en vez de su propio día real. Con la
        # ventana de 24-48h de fetch_news.py, en el flujo diario normal
        # ambas fechas casi siempre coinciden -- esto solo cambia algo en
        # el caso borde de un ítem con fecha de publicación real distinta a
        # la de esta corrida (fue justo lo que le pasó a 8 noticias del 5
        # de septiembre que quedaron archivadas bajo el 6; ver
        # scripts/reorganizar_archivo_por_fecha_real.py para la
        # reorganización puntual del histórico que ya estaba mal archivado
        # antes de este fix).
        grupos_por_fecha: dict[str, list[dict]] = {}
        for item in nuevos_ciber:
            clave_fecha = fecha_publicacion_gye(item) or fecha_str
            grupos_por_fecha.setdefault(clave_fecha, []).append(item)

        ARCHIVO_DIR.mkdir(parents=True, exist_ok=True)

        # Se procesa de la fecha más antigua a la más reciente -- así, al
        # terminar el bucle, la última edición escrita es la más reciente, y
        # la portada (armada después del bucle) refleja esa edición sin
        # importar si coincide o no con fecha_str.
        ruta_dia_mas_reciente: Path | None = None
        subtitulo_mas_reciente = ""
        descripcion_mas_reciente = ""
        imagen_og_mas_reciente = ""

        for clave_fecha in sorted(grupos_por_fecha.keys()):
            items_del_dia = grupos_por_fecha[clave_fecha]
            fecha_dia_dt = datetime.strptime(clave_fecha, "%Y-%m-%d").replace(hour=12, tzinfo=ZONA_GUAYAQUIL)
            subtitulo_dia = f"Edición del {fecha_legible(fecha_dia_dt)}"

            # La noticia más reciente de ESTE día va destacada arriba en
            # grande; el resto forma la cuadrícula de tarjetas debajo.
            items_html_dia = _renderizar_grid(items_del_dia, rutas_noticia)

            # Descripción/imagen para SEO y Open Graph de esta edición: se
            # basan en su noticia destacada (la más reciente de ese día), o
            # en el logo si esa noticia no tiene imagen propia.
            descripcion_dia = (
                f"Titulares de ciberseguridad del {fecha_legible(fecha_dia_dt)}, agregados de fuentes públicas "
                "verificadas (The Hacker News, BleepingComputer, Krebs on Security y más). "
                "Un proyecto de DERENZIN S.A.S."
            )
            imagen_og_dia = items_del_dia[0].get("imagen_local") or "/assets/logo-derenzin.png"

            # Página de archivo de este día -- se escribe PRIMERO, porque si
            # ya existía una edición de ese mismo día (p.ej. varias corridas
            # manuales, o un ítem atrasado que cae en un día que ya tenía
            # noticias reales) las noticias nuevas se ANEXAN a la cuadrícula
            # existente en vez de reemplazarla; la destacada de la primera
            # edición de ese día se queda como está, no cambia con cada
            # corrida posterior.
            pagina_dia = render_pagina_archivo_dia(clave_fecha, subtitulo_dia, items_html_dia, descripcion_dia, imagen_og_dia)
            ruta_dia = ARCHIVO_DIR / f"{clave_fecha}.html"
            if ruta_dia.exists():
                anterior = ruta_dia.read_text(encoding="utf-8")
                marcador_fin_grid = "    <!-- FIN-GRID -->\n"
                if marcador_fin_grid in anterior:
                    tarjetas_nuevas_html = "".join(
                        render_tarjeta_html(item, rutas_noticia[item["enlace"]], es_destacada=False) for item in items_del_dia
                    )
                    anterior = anterior.replace(marcador_fin_grid, tarjetas_nuevas_html + marcador_fin_grid, 1)
                    ruta_dia.write_text(anterior, encoding="utf-8")
                    log(f"Actualizado {ruta_dia} (ya existía una edición de ese día; se anexaron los ítems nuevos a la cuadrícula).")
                else:
                    ruta_dia.write_text(pagina_dia, encoding="utf-8")
                    log(f"Reescrito {ruta_dia} (no se pudo anexar de forma segura; probablemente tenía el diseño anterior).")
            else:
                ruta_dia.write_text(pagina_dia, encoding="utf-8")
                log(f"Escrito {ruta_dia}")

            ruta_dia_mas_reciente = ruta_dia
            subtitulo_mas_reciente = subtitulo_dia
            descripcion_mas_reciente = descripcion_dia
            imagen_og_mas_reciente = imagen_og_dia

        # Portada (index.html): SIEMPRE refleja el mismo contenido que acaba
        # de quedar en el archivo de la edición MÁS RECIENTE (no solo los
        # ítems nuevos de esta corrida, y no necesariamente la de fecha_str,
        # si esta corrida solo trajo ítems atrasados de un día anterior).
        # Se relee el archivo recién escrito y se reusa su cuadrícula
        # completa, para que portada y archivo sean siempre el mismo
        # contenido -- así, si hubo más de una corrida el mismo día (p.ej.
        # varios workflow_dispatch manuales), la portada nunca queda
        # mostrando solo la última corrida mientras el archivo ya tiene todo
        # acumulado.
        assert ruta_dia_mas_reciente is not None  # nuevos_ciber no está vacío acá, así que el bucle corrió al menos una vez
        contenido_dia = ruta_dia_mas_reciente.read_text(encoding="utf-8")
        m_items = re.search(r'<h1 class="sr-only">.*?</h1>\n(.*?)\n  </main>', contenido_dia, re.DOTALL)
        items_html_portada = m_items.group(1) if m_items else ""

        index_html = render_pagina_index(
            "Noticias de Ciberseguridad — Portada", subtitulo_mas_reciente, items_html_portada, descripcion_mas_reciente, imagen_og_mas_reciente
        )
        with open(SITE_DIR / "index.html", "w", encoding="utf-8") as f:
            f.write(index_html)
        log(f"Escrito {SITE_DIR / 'index.html'}")
    else:
        log("Sin noticias nuevas de ciberseguridad hoy: index.html y el archivo del día no se tocan.")

    # 3. Índice de archivo (siempre, es barato y solo escanea lo ya existente)
    ARCHIVO_DIR.mkdir(parents=True, exist_ok=True)
    dias_existentes = sorted({p.stem for p in ARCHIVO_DIR.glob("*.html") if p.stem != "index"})
    with open(ARCHIVO_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(render_archivo_index(dias_existentes))
    log(f"Escrito {ARCHIVO_DIR / 'index.html'} ({len(dias_existentes)} edición/ediciones listadas)")

    # 4. Sección "Protección de Datos": a diferencia de la portada de
    # ciberseguridad, esta se reconstruye SIEMPRE (aunque hoy no haya
    # novedades de esta sección) a partir de una lista propia de los
    # últimos ítems -- ver PROTECCION_DATOS_JSON -- para que la página
    # nunca quede vacía solo porque hoy no salió nada nuevo de esta
    # sección en particular, y siempre muestre el bloque de la SPDP.
    recientes_proteccion = cargar_recientes_proteccion_datos()
    if nuevos_proteccion:
        # Se guarda también la ruta de detalle ya asignada (rutas_noticia),
        # para no tener que re-derivarla en runs futuros -- nombre_archivo_noticia()
        # usa fecha_str (el día del RUN, no el de publicación del artículo),
        # así que recalcularla más adelante con la fecha de publicación del
        # ítem daría una ruta distinta a la que realmente se escribió.
        for item in nuevos_proteccion:
            item["ruta_noticia"] = rutas_noticia[item["enlace"]]
        # Los nuevos van primero (más recientes), sin duplicar por enlace.
        enlaces_nuevos = {item["enlace"] for item in nuevos_proteccion}
        recientes_proteccion = nuevos_proteccion + [
            item for item in recientes_proteccion if item["enlace"] not in enlaces_nuevos
        ]
        recientes_proteccion = recientes_proteccion[:MAX_RECIENTES_PROTECCION_DATOS]
        guardar_recientes_proteccion_datos(recientes_proteccion)
        log(f"{len(nuevos_proteccion)} noticia(s) nueva(s) de Protección de Datos; lista reciente ahora tiene {len(recientes_proteccion)}.")

    PROTECCION_DATOS_DIR.mkdir(parents=True, exist_ok=True)
    rutas_proteccion = {item["enlace"]: item["ruta_noticia"] for item in recientes_proteccion}

    if recientes_proteccion:
        items_html_proteccion = _renderizar_grid(recientes_proteccion, rutas_proteccion)
        descripcion_proteccion = (
            "Noticias y novedades sobre protección de datos personales y la LOPDP en "
            "Ecuador, agregadas de fuentes públicas verificadas. Un proyecto de DERENZIN S.A.S."
        )
        imagen_og_proteccion = recientes_proteccion[0].get("imagen_local") or "/assets/logo-derenzin.png"
    else:
        items_html_proteccion = '    <p class="sin-noticias">Todavía no hay noticias publicadas en esta sección.</p>\n'
        descripcion_proteccion = (
            "Sección de protección de datos personales y la LOPDP en Ecuador. "
            "Un proyecto de DERENZIN S.A.S."
        )
        imagen_og_proteccion = "/assets/logo-derenzin.png"

    pagina_proteccion = render_pagina_seccion_proteccion_datos(items_html_proteccion, descripcion_proteccion, imagen_og_proteccion)
    with open(PROTECCION_DATOS_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(pagina_proteccion)
    log(f"Escrito {PROTECCION_DATOS_DIR / 'index.html'} ({len(recientes_proteccion)} noticia(s) en la sección).")

    # 5. Sitemap (para buscadores) — se regenera completo cada vez que hay publicación
    generar_sitemap()

    # 5b. Blindaje de categoría: un build normal nunca desincroniza
    # categoria-badge/eyebrow-categoria (ver sincronizar_badges_categoria()),
    # pero scripts de backfill puntuales que tocan HTML ya publicado sí
    # pueden hacerlo -- correrlo acá de forma incondicional deja el sitio
    # siempre consistente sin depender de que alguien se acuerde de
    # invocar --reparar-badges por separado.
    reparar_badges_categoria_en_sitio()

    # 6. Ledger de publicadas (unificado: ambas secciones comparten el mismo
    # ledger, para no duplicar noticias en ninguna de las dos)
    publicadas = cargar_publicadas()
    urls = publicadas.setdefault("urls", {})
    ahora_iso = datetime.now(timezone.utc).isoformat()
    for item in nuevos:
        ruta = rutas_noticia.get(item["enlace"], "")
        urls[item["enlace"]] = {
            "guid": item["guid"],
            "fuente": item["fuente"],
            "titulo": item["titulo"],
            "fecha_publicacion_iso": item["fecha_publicacion_iso"],
            "fecha_agregada_iso": ahora_iso,
            "ruta_noticia": ruta,
        }
        # Si fetch_news.py fusionó este ítem con otras fuentes que cubrían
        # el mismo hecho (ver fusionar_mismo_hecho()), esas fuentes
        # "absorbidas" también quedan registradas en el ledger -- apuntando
        # a la MISMA página de detalle -- para que un run futuro las
        # reconozca como ya publicadas y no las vuelva a traer sueltas.
        for extra in item.get("fuentes_adicionales") or []:
            urls[extra["enlace"]] = {
                "guid": extra["enlace"],
                "fuente": extra["nombre"],
                "titulo": item["titulo"],
                "fecha_publicacion_iso": item["fecha_publicacion_iso"],
                "fecha_agregada_iso": ahora_iso,
                "ruta_noticia": ruta,
            }
    guardar_publicadas(publicadas)
    log(f"Ledger actualizado: {PUBLICADAS_JSON} ahora tiene {len(urls)} URL(s) registradas.")

    log(f"Listo: {len(nuevos)} noticia(s) publicada(s) el {fecha_str} ({len(nuevos_ciber)} ciberseguridad, {len(nuevos_proteccion)} protección de datos).")


if __name__ == "__main__":
    main()
