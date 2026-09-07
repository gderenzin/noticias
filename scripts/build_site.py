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

import hashlib
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

from texto import acotar_parrafos, fuente_parece_incompleta, primeras_oraciones, rematar_final, truncar

RAIZ = Path(__file__).resolve().parent.parent
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"
PUBLICADAS_JSON = RAIZ / "data" / "publicadas.json"
SITE_DIR = RAIZ / "site"
ARCHIVO_DIR = SITE_DIR / "archivo"
NOTICIA_DIR = SITE_DIR / "noticia"

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


def render_meta_seo(titulo: str, descripcion: str, ruta_canonica: str, ruta_imagen: str, tipo_og: str = "website") -> str:
    """Bloque de <meta> compartido por las 4 plantillas: description,
    canonical, Open Graph y Twitter Card. `ruta_canonica` y `ruta_imagen`
    pueden ser relativas a la raíz del sitio (se resuelven con
    url_absoluta) o ya vernir absolutas."""
    url_canonica = url_absoluta(ruta_canonica)
    url_imagen = url_absoluta(ruta_imagen)
    desc = escape(descripcion, quote=True)
    tit = escape(titulo, quote=True)
    return f"""  <meta name="description" content="{desc}">
  <link rel="canonical" href="{escape(url_canonica, quote=True)}">
  <meta property="og:type" content="{tipo_og}">
  <meta property="og:site_name" content="Periódico de Ciberseguridad">
  <meta property="og:title" content="{tit}">
  <meta property="og:description" content="{desc}">
  <meta property="og:url" content="{escape(url_canonica, quote=True)}">
  <meta property="og:image" content="{escape(url_imagen, quote=True)}">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{tit}">
  <meta name="twitter:description" content="{desc}">
  <meta name="twitter:image" content="{escape(url_imagen, quote=True)}">"""


def render_json_ld_noticia(item: dict, titulo_mostrar: str, ruta_noticia: str, categoria: str) -> str:
    """Datos estructurados schema.org/NewsArticle para la página de detalle.
    Todos los campos salen tal cual de los datos ya verificados del RSS —
    nada inventado. "author" es la fuente original (no tenemos el nombre de
    un periodista individual en el RSS); "publisher" es este sitio."""
    ruta_imagen = item.get("imagen_local") or "/assets/logo-derenzin.png"
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
            "name": "Periódico de Ciberseguridad — DERENZIN S.A.S.",
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
    """Clasifica la noticia por palabras clave en el texto ORIGINAL (sin
    traducir) del título+extracto, para no depender de la calidad de la
    traducción. Nunca inventa una categoría que no se deduzca del texto; si
    no coincide ninguna, usa la categoría genérica."""
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

    if ruta_imagen:
        alt = escape(titulo_mostrar, quote=True)
        src = escape(ruta_imagen, quote=True)
        return f"""      <figure class="noticia-imagen{clase_extra}">
        {badge}
        <img src="{src}" alt="{alt}" loading="lazy" decoding="async">
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
          <img class="icono-generico" src="/assets/iconos/{categoria}.svg" alt="{alt_icono}" loading="lazy" decoding="async">
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
    imagen_pagina = item.get("imagen_local") or "/assets/logo-derenzin.png"

    parrafos_html = "\n".join(
        f"        <p>{escape(p)}</p>" for p in ampliado["parrafos"]
    )

    # Puede haber dos notas de idioma distintas: la del título (tarjeta) y la
    # del resumen ampliado (traducciones independientes, cada una con su
    # propio intento). Si coinciden en texto, se muestra una sola vez.
    notas = [n for n in {nota_idioma_titulo, nota_idioma_ampliado} if n]
    notas_html = "".join(f'<span class="idioma-nota">{escape(n)}</span>' for n in notas)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="{POLITICA_SEGURIDAD_CONTENIDO}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{titulo_html} — Periódico de Ciberseguridad</title>
{render_meta_seo(f"{titulo_mostrar} — Periódico de Ciberseguridad", descripcion, ruta_noticia, imagen_pagina, tipo_og="article")}
  <link rel="icon" href="../assets/favicon.png">
  <meta name="theme-color" content="#00b8d4">
{ENLACES_FUENTE}
  <link rel="stylesheet" href="../style.css">
{render_json_ld_noticia(item, titulo_mostrar, ruta_noticia, categoria)}
</head>
<body>
{render_cabecera("Detalle de la noticia", "../", "noticia")}
  <main class="contenido pagina-noticia">
    <article class="noticia-detalle cat-{categoria}">
      <span class="eyebrow-categoria">{escape(info_categoria["etiqueta"])}</span>
      <h1 class="noticia-detalle-titulo">{titulo_html}</h1>
      <div class="noticia-meta">
        <span class="fuente">Fuente: {fuente}</span>
        <span class="fecha">Publicado: {fecha_str}</span>
        {notas_html}
      </div>
{render_botones_compartir(titulo_mostrar, ruta_noticia)}{imagen_html}      <div class="noticia-detalle-cuerpo">
{parrafos_html}
      </div>
      <div class="noticia-fuente-final">
        <p>Fuente: <a href="{enlace_externo}" target="_blank" rel="noopener noreferrer">{fuente}</a></p>
        <a class="destacada-cta" href="{enlace_externo}" target="_blank" rel="noopener noreferrer">Leer el artículo original completo en {fuente} ↗</a>
      </div>
    </article>
  </main>

{render_pie("../")}
  <script src="../assets/compartir.js" defer></script>
</body>
</html>
"""


FUENTES_MONITOREADAS = "The Hacker News, BleepingComputer, Krebs on Security, Dark Reading, WeLiveSecurity (ESET), INCIBE-CERT."


def render_cabecera(subtitulo: str, prefijo: str, pagina_actual: str) -> str:
    """`prefijo`: '' en site/index.html, '../' en site/archivo/*.html.
    `pagina_actual`: 'portada' o 'archivo', para resaltar el link activo."""
    nav_portada_clase = ' class="activo"' if pagina_actual == "portada" else ""
    nav_archivo_clase = ' class="activo"' if pagina_actual == "archivo" else ""
    # El link "Archivo" apunta a site/archivo/index.html. Desde site/index.html
    # eso es "archivo/index.html"; desde dentro de site/archivo/ (donde viven
    # tanto el índice de archivo como cada día) es simplemente "index.html".
    href_archivo = "index.html" if prefijo == "../" else f"{prefijo}archivo/index.html"
    return f"""  <header class="cabecera">
    <div class="cabecera-contenido">
      <div class="marca">
        <a href="{prefijo}index.html" class="marca-enlace">
          <img src="{prefijo}assets/logo-derenzin.png" alt="DERENZIN" class="marca-logo">
          <div class="marca-texto">
            <span class="marca-titulo">Periódico de Ciberseguridad</span>
            <span class="marca-byline">Un proyecto de <strong>DERENZIN S.A.S.</strong></span>
          </div>
        </a>
        <a href="https://derenzin.com" target="_blank" rel="noopener noreferrer" class="enlace-derenzin">derenzin.com ↗</a>
      </div>
      <p class="subtitulo">{escape(subtitulo)}</p>
      <nav class="nav">
        <a href="{prefijo}index.html"{nav_portada_clase}>Inicio</a>
        <a href="{href_archivo}"{nav_archivo_clase}>Archivo</a>
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


def render_archivo_index(dias: list[str]) -> str:
    if dias:
        filas = "\n".join(
            f'      <li><a href="{d}.html">{fecha_legible(datetime.strptime(d, "%Y-%m-%d"))}</a></li>'
            for d in sorted(dias, reverse=True)
        )
        lista = f"<ul class=\"lista-archivo\">\n{filas}\n    </ul>"
    else:
        lista = "<p>Todavía no hay ediciones archivadas.</p>"

    titulo_pagina = "Archivo — Periódico de Ciberseguridad"
    descripcion = "Índice de todas las ediciones diarias publicadas del Periódico de Ciberseguridad — un proyecto de DERENZIN S.A.S."
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


def main() -> None:
    nuevos = cargar_nuevas()

    if not nuevos:
        log("Sin novedades hoy: no se genera ninguna página nueva ni se toca el ledger.")
        return

    if not DEEPL_API_KEY:
        log("AVISO GENERAL: DEEPL_API_KEY no está configurada. Las noticias en inglés se publicarán citando el título/extracto original (sin traducir) en vez de fallar o inventar una traducción.")

    ahora_gye = datetime.now(ZONA_GUAYAQUIL)
    fecha_str = ahora_gye.strftime("%Y-%m-%d")
    # Nota: a propósito NO se muestra el conteo de ítems en el HTML — ese
    # número es información de diagnóstico del proceso, no algo para el
    # lector; el conteo real ya queda en el log del workflow de GitHub Actions.
    subtitulo = f"Edición del {fecha_legible(ahora_gye)}"

    # Cada noticia tiene su propia página de detalle dentro del sitio
    # (site/noticia/AAAA-MM-DD-slug-hash.html); todos los enlaces de portada,
    # cuadrícula y archivo apuntan ahí — no directo a la fuente externa (esa
    # solo aparece al pie de la página de detalle).
    NOTICIA_DIR.mkdir(parents=True, exist_ok=True)
    rutas_noticia: dict[str, str] = {}
    for item in nuevos:
        nombre_archivo = nombre_archivo_noticia(item, fecha_str)
        rutas_noticia[item["enlace"]] = f"/noticia/{nombre_archivo}"
        pagina_noticia = render_pagina_noticia(item, rutas_noticia[item["enlace"]])
        with open(NOTICIA_DIR / nombre_archivo, "w", encoding="utf-8") as f:
            f.write(pagina_noticia)
    log(f"Generadas {len(rutas_noticia)} página(s) de detalle en {NOTICIA_DIR}.")

    # La noticia más reciente va destacada arriba en grande; el resto forma
    # la cuadrícula de tarjetas debajo (ver render_tarjeta_html).
    destacada_html = render_tarjeta_html(nuevos[0], rutas_noticia[nuevos[0]["enlace"]], es_destacada=True)
    tarjetas_html = "".join(
        render_tarjeta_html(item, rutas_noticia[item["enlace"]], es_destacada=False) for item in nuevos[1:]
    )
    titulo_seccion = '    <h2 class="seccion-titulo">Últimas noticias</h2>\n' if nuevos[1:] else ""
    items_html = (
        destacada_html
        + titulo_seccion
        + f"""
    <div class="grid-noticias">
{tarjetas_html}    </div>
    <!-- FIN-GRID -->
"""
    )

    # Descripción/imagen para SEO y Open Graph de portada y archivo del día:
    # se basan en la noticia destacada (la más reciente), o en el logo si esa
    # noticia no tiene imagen propia.
    descripcion_edicion = (
        f"Titulares de ciberseguridad del {fecha_legible(ahora_gye)}, agregados de fuentes públicas "
        "verificadas (The Hacker News, BleepingComputer, Krebs on Security y más). "
        "Un proyecto de DERENZIN S.A.S."
    )
    imagen_og_edicion = nuevos[0].get("imagen_local") or "/assets/logo-derenzin.png"

    # 1. Portada (index.html)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    index_html = render_pagina_index(
        "Periódico de Ciberseguridad — Portada", subtitulo, items_html, descripcion_edicion, imagen_og_edicion
    )
    with open(SITE_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(index_html)
    log(f"Escrito {SITE_DIR / 'index.html'}")

    # 2. Página de archivo del día
    ARCHIVO_DIR.mkdir(parents=True, exist_ok=True)
    pagina_dia = render_pagina_archivo_dia(fecha_str, subtitulo, items_html, descripcion_edicion, imagen_og_edicion)
    ruta_dia = ARCHIVO_DIR / f"{fecha_str}.html"
    if ruta_dia.exists():
        # Ya hubo una edición hoy (p.ej. se corrió manualmente dos veces): la
        # noticia destacada de esa primera edición se queda como está, y las
        # nuevas se anexan como tarjetas adicionales al final de la
        # cuadrícula existente — no se sobreescribe lo ya publicado.
        anterior = ruta_dia.read_text(encoding="utf-8")
        marcador_fin_grid = "    <!-- FIN-GRID -->\n"
        if marcador_fin_grid in anterior:
            tarjetas_nuevas_html = "".join(
                render_tarjeta_html(item, rutas_noticia[item["enlace"]], es_destacada=False) for item in nuevos
            )
            anterior = anterior.replace(marcador_fin_grid, tarjetas_nuevas_html + marcador_fin_grid, 1)
            ruta_dia.write_text(anterior, encoding="utf-8")
            log(f"Actualizado {ruta_dia} (ya existía una edición de hoy; se anexaron los ítems nuevos a la cuadrícula).")
        else:
            ruta_dia.write_text(pagina_dia, encoding="utf-8")
            log(f"Reescrito {ruta_dia} (no se pudo anexar de forma segura; probablemente tenía el diseño anterior).")
    else:
        ruta_dia.write_text(pagina_dia, encoding="utf-8")
        log(f"Escrito {ruta_dia}")

    # 3. Índice de archivo
    dias_existentes = sorted({p.stem for p in ARCHIVO_DIR.glob("*.html") if p.stem != "index"})
    with open(ARCHIVO_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(render_archivo_index(dias_existentes))
    log(f"Escrito {ARCHIVO_DIR / 'index.html'} ({len(dias_existentes)} edición/ediciones listadas)")

    # 4. Sitemap (para buscadores) — se regenera completo cada vez que hay publicación
    generar_sitemap()

    # 5. Ledger de publicadas
    publicadas = cargar_publicadas()
    urls = publicadas.setdefault("urls", {})
    for item in nuevos:
        urls[item["enlace"]] = {
            "guid": item["guid"],
            "fuente": item["fuente"],
            "titulo": item["titulo"],
            "fecha_publicacion_iso": item["fecha_publicacion_iso"],
            "fecha_agregada_iso": datetime.now(timezone.utc).isoformat(),
            "ruta_noticia": rutas_noticia.get(item["enlace"], ""),
        }
    guardar_publicadas(publicadas)
    log(f"Ledger actualizado: {PUBLICADAS_JSON} ahora tiene {len(urls)} URL(s) registradas.")

    log(f"Listo: {len(nuevos)} noticia(s) publicada(s) en la edición del {fecha_str}.")


if __name__ == "__main__":
    main()
