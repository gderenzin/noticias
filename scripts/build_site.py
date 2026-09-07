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

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"
PUBLICADAS_JSON = RAIZ / "data" / "publicadas.json"
SITE_DIR = RAIZ / "site"
ARCHIVO_DIR = SITE_DIR / "archivo"

# Guayaquil = UTC-5 todo el año (Ecuador no usa horario de verano)
ZONA_GUAYAQUIL = timezone(timedelta(hours=-5))

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


def truncar(texto: str, maximo: int) -> str:
    if len(texto) <= maximo:
        return texto
    cortado = texto[:maximo].rsplit(" ", 1)[0]
    return cortado + "…"


# ---------------------------------------------------------------------------
# Traducción (DeepL) — nunca inventa: si falla, devuelve None y quien la llama
# decide el fallback seguro (citar el original).
# ---------------------------------------------------------------------------

def _endpoint_deepl(api_key: str) -> str:
    # Las claves del plan gratuito de DeepL terminan en ":fx" y usan un host distinto.
    return "https://api-free.deepl.com/v2/translate" if api_key.endswith(":fx") else "https://api.deepl.com/v2/translate"


def traducir_deepl(texto: str, idioma_origen: str) -> str | None:
    """Traduce `texto` al español usando la API de DeepL. Devuelve None (sin
    lanzar excepción) si no hay clave configurada o si la llamada falla por
    cualquier motivo — nunca se fabrica una traducción alternativa."""
    if not texto or not DEEPL_API_KEY:
        return None

    datos = urllib.parse.urlencode(
        {
            "auth_key": DEEPL_API_KEY,
            "text": texto,
            "source_lang": idioma_origen.upper(),
            "target_lang": "ES",
        }
    ).encode("utf-8")

    req = urllib.request.Request(_endpoint_deepl(DEEPL_API_KEY), data=datos, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=DEEPL_TIMEOUT_SEGUNDOS) as resp:
            if resp.status != 200:
                log(f"  AVISO: DeepL respondió HTTP {resp.status}; se usa el original citado para este ítem.")
                return None
            cuerpo = json.loads(resp.read().decode("utf-8"))
        traducciones = cuerpo.get("translations") or []
        if not traducciones:
            return None
        return traducciones[0].get("text") or None
    except urllib.error.HTTPError as ex:
        log(f"  AVISO: DeepL HTTPError {ex.code}; se usa el original citado para este ítem.")
    except urllib.error.URLError as ex:
        log(f"  AVISO: DeepL URLError ({ex.reason}); se usa el original citado para este ítem.")
    except TimeoutError:
        log("  AVISO: timeout llamando a DeepL; se usa el original citado para este ítem.")
    except (json.JSONDecodeError, KeyError, IndexError) as ex:
        log(f"  AVISO: respuesta inesperada de DeepL ({ex}); se usa el original citado para este ítem.")
    except Exception as ex:  # noqa: BLE001 - una falla de traducción nunca debe tumbar el build
        log(f"  AVISO: error inesperado traduciendo con DeepL ({type(ex).__name__}: {ex}); se usa el original citado.")
    return None


def preparar_texto_mostrado(item: dict) -> dict:
    """Devuelve un dict con los campos ya listos para mostrar, siempre en
    español, sin inventar contenido:
      - titulo_mostrar
      - resumen_mostrar
      - nota_idioma: texto corto para la interfaz (o cadena vacía)
    """
    titulo_original = item["titulo"].strip()
    idioma = item.get("idioma", "en")
    extracto_original = (item.get("extracto_original") or "").strip()
    aporta_info = bool(extracto_original) and extracto_original.lower() != titulo_original.lower() and len(extracto_original) > 15

    if idioma == "es":
        resumen = truncar(extracto_original, 600) if aporta_info else "El RSS de la fuente no trae un extracto adicional; solo se dispone del titular."
        return {"titulo_mostrar": titulo_original, "resumen_mostrar": resumen, "nota_idioma": ""}

    # Fuente en idioma distinto al español: intentar traducir con DeepL.
    if not DEEPL_API_KEY:
        log(f"  AVISO: DEEPL_API_KEY no configurada; '{titulo_original[:60]}...' se muestra citando el original.")

    titulo_traducido = traducir_deepl(titulo_original, idioma)
    extracto_traducido = traducir_deepl(truncar(extracto_original, 700), idioma) if aporta_info else None

    if titulo_traducido:
        resumen = truncar(extracto_traducido, 600) if extracto_traducido else "El RSS de la fuente no trae un extracto adicional aparte del titular."
        nota = "Traducido automáticamente del inglés (DeepL)." if idioma == "en" else f"Traducido automáticamente del {idioma} (DeepL)."
        return {"titulo_mostrar": titulo_traducido, "resumen_mostrar": resumen, "nota_idioma": nota}

    # Fallback seguro: no se pudo traducir (sin clave o falló la API). Nunca
    # se inventa una traducción; se cita el original con una nota clara.
    partes = [f'Título original: "{titulo_original}".']
    if aporta_info:
        partes.append(f'Extracto original: "{truncar(extracto_original, 500)}"')
    partes.append("(No se pudo traducir automáticamente esta noticia; se muestra el texto original.)")
    return {
        "titulo_mostrar": titulo_original,
        "resumen_mostrar": " ".join(partes),
        "nota_idioma": "No se pudo traducir automáticamente (se muestra el original).",
    }


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
    return f'<span class="categoria-badge" style="--color-categoria: {info["color"]}">{escape(info["etiqueta"])}</span>'


def render_imagen_html(item: dict, categoria: str, titulo_mostrar: str, destacada: bool = False) -> str:
    """Imagen real (ya descargada por fetch_news.py a site/imagenes/…) o, si no
    hay ninguna, el ícono de categoría de respaldo. Ambas llevan siempre la
    insignia de color de la categoría encima."""
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
    etiqueta = escape(info["etiqueta"])
    color = info["color"]
    alt_icono = escape(f"Ilustración genérica de la categoría {info['etiqueta']}; no es una foto real del hecho", quote=True)
    return f"""      <figure class="noticia-imagen noticia-imagen--generica{clase_extra}" style="--color-categoria: {color}">
        {badge}
        <div class="icono-generico" role="img" aria-label="{alt_icono}">{info['svg']}</div>
        <figcaption>Ilustración genérica: {etiqueta} (no es una foto real del hecho)</figcaption>
      </figure>
"""


def render_tarjeta_html(item: dict, es_destacada: bool = False) -> str:
    """Renderiza una noticia como tarjeta de grid (por defecto) o, si
    `es_destacada`, como el bloque grande de "lo más reciente" arriba de la
    portada/edición del día."""
    mostrado = preparar_texto_mostrado(item)
    titulo_mostrar = mostrado["titulo_mostrar"]
    resumen_mostrar = mostrado["resumen_mostrar"]
    nota_idioma = mostrado["nota_idioma"]

    categoria = categorizar(item)
    imagen_html = render_imagen_html(item, categoria, titulo_mostrar, destacada=es_destacada)

    fuente = escape(item["fuente"])
    enlace = escape(item["enlace"], quote=True)
    fecha_str = escape(fecha_corta(item["fecha_publicacion_iso"]))
    resumen_html = escape(resumen_mostrar).replace("\n", "<br>")
    titulo_html = escape(titulo_mostrar)
    nota_html = f'<span class="idioma-nota">{escape(nota_idioma)}</span>' if nota_idioma else ""

    if es_destacada:
        return f"""    <section class="destacada">
      <a class="destacada-imagen-enlace" href="{enlace}" rel="noopener noreferrer" target="_blank">
{imagen_html}      </a>
      <div class="destacada-cuerpo">
        <span class="destacada-eyebrow">Lo más reciente</span>
        <h2 class="destacada-titulo"><a href="{enlace}" rel="noopener noreferrer" target="_blank">{titulo_html}</a></h2>
        <p class="destacada-resumen">{resumen_html}</p>
        <div class="noticia-meta">
          <span class="fuente">Fuente: {fuente}</span>
          <span class="fecha">Publicado: {fecha_str}</span>
          {nota_html}
        </div>
        <a class="destacada-cta" href="{enlace}" rel="noopener noreferrer" target="_blank">Leer la noticia completa →</a>
      </div>
    </section>
"""

    return f"""      <article class="tarjeta">
        <a class="tarjeta-imagen-enlace" href="{enlace}" rel="noopener noreferrer" target="_blank">
{imagen_html}        </a>
        <div class="tarjeta-cuerpo">
          <h3 class="tarjeta-titulo"><a href="{enlace}" rel="noopener noreferrer" target="_blank">{titulo_html}</a></h3>
          <p class="tarjeta-resumen">{resumen_html}</p>
          <div class="noticia-meta">
            <span class="fuente">Fuente: {fuente}</span>
            <span class="fecha">Publicado: {fecha_str}</span>
            {nota_html}
          </div>
        </div>
      </article>
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


def render_pagina_index(titulo_pagina: str, subtitulo: str, items_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(titulo_pagina)}</title>
  <meta name="description" content="Periódico digital de ciberseguridad: titulares diarios con enlace directo a la fuente original. Un proyecto de DERENZIN S.A.S.">
  <link rel="icon" href="assets/favicon.png">
  <meta name="theme-color" content="#00b8d4">
{ENLACES_FUENTE}
  <link rel="stylesheet" href="style.css">
</head>
<body>
{render_cabecera(subtitulo, "", "portada")}
  <main class="contenido portada">
{items_html}
  </main>

{render_pie("")}
</body>
</html>
"""


def render_pagina_archivo_dia(fecha_str: str, subtitulo: str, items_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Ciberseguridad — edición del {escape(fecha_str)}</title>
  <meta name="description" content="Periódico digital de ciberseguridad: titulares diarios con enlace directo a la fuente original. Un proyecto de DERENZIN S.A.S.">
  <link rel="icon" href="../assets/favicon.png">
  <meta name="theme-color" content="#00b8d4">
{ENLACES_FUENTE}
  <link rel="stylesheet" href="../style.css">
</head>
<body>
{render_cabecera(subtitulo, "../", "archivo")}
  <main class="contenido archivo-dia">
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

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Archivo — Periódico de Ciberseguridad</title>
  <link rel="icon" href="../assets/favicon.png">
  <meta name="theme-color" content="#00b8d4">
{ENLACES_FUENTE}
  <link rel="stylesheet" href="../style.css">
</head>
<body>
{render_cabecera("Archivo de ediciones anteriores", "../", "archivo")}
  <main class="contenido">
    {lista}
  </main>

{render_pie("../")}
</body>
</html>
"""


def main() -> None:
    nuevos = cargar_nuevas()

    if not nuevos:
        log("Sin novedades hoy: no se genera ninguna página nueva ni se toca el ledger.")
        return

    if not DEEPL_API_KEY:
        log("AVISO GENERAL: DEEPL_API_KEY no está configurada. Las noticias en inglés se publicarán citando el título/extracto original (sin traducir) en vez de fallar o inventar una traducción.")

    ahora_gye = datetime.now(ZONA_GUAYAQUIL)
    fecha_str = ahora_gye.strftime("%Y-%m-%d")
    subtitulo = f"Edición del {fecha_legible(ahora_gye)} — {len(nuevos)} noticia(s) nueva(s)"

    # La noticia más reciente va destacada arriba en grande; el resto forma
    # la cuadrícula de tarjetas debajo (ver render_tarjeta_html).
    destacada_html = render_tarjeta_html(nuevos[0], es_destacada=True)
    tarjetas_html = "".join(render_tarjeta_html(item, es_destacada=False) for item in nuevos[1:])
    items_html = (
        destacada_html
        + f"""
    <div class="grid-noticias">
{tarjetas_html}    </div>
    <!-- FIN-GRID -->
"""
    )

    # 1. Portada (index.html)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    index_html = render_pagina_index("Periódico de Ciberseguridad — Portada", subtitulo, items_html)
    with open(SITE_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(index_html)
    log(f"Escrito {SITE_DIR / 'index.html'}")

    # 2. Página de archivo del día
    ARCHIVO_DIR.mkdir(parents=True, exist_ok=True)
    pagina_dia = render_pagina_archivo_dia(fecha_str, subtitulo, items_html)
    ruta_dia = ARCHIVO_DIR / f"{fecha_str}.html"
    if ruta_dia.exists():
        # Ya hubo una edición hoy (p.ej. se corrió manualmente dos veces): la
        # noticia destacada de esa primera edición se queda como está, y las
        # nuevas se anexan como tarjetas adicionales al final de la
        # cuadrícula existente — no se sobreescribe lo ya publicado.
        anterior = ruta_dia.read_text(encoding="utf-8")
        marcador_fin_grid = "    <!-- FIN-GRID -->\n"
        if marcador_fin_grid in anterior:
            tarjetas_nuevas_html = "".join(render_tarjeta_html(item, es_destacada=False) for item in nuevos)
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

    # 4. Ledger de publicadas
    publicadas = cargar_publicadas()
    urls = publicadas.setdefault("urls", {})
    for item in nuevos:
        urls[item["enlace"]] = {
            "guid": item["guid"],
            "fuente": item["fuente"],
            "titulo": item["titulo"],
            "fecha_publicacion_iso": item["fecha_publicacion_iso"],
            "fecha_agregada_iso": datetime.now(timezone.utc).isoformat(),
        }
    guardar_publicadas(publicadas)
    log(f"Ledger actualizado: {PUBLICADAS_JSON} ahora tiene {len(urls)} URL(s) registradas.")

    log(f"Listo: {len(nuevos)} noticia(s) publicada(s) en la edición del {fecha_str}.")


if __name__ == "__main__":
    main()
