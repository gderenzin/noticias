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

Imágenes: cuando el RSS trae una imagen propia, se DESCARGA y se guarda como
archivo local dentro de site/imagenes/AAAA-MM-DD/ (en vez de enlazar directo a
la URL externa — el hotlinking es frágil: bloqueadores de anuncios, filtros de
red corporativos o el propio origen pueden cortar la imagen en cualquier
momento). Si la descarga falla por cualquier motivo, o el RSS no trae imagen,
el ítem queda sin imagen local y build_site.py usa el ícono de categoría de
respaldo — nunca se inventa ni se sustituye por otra imagen. Cada imagen ya
descargada se registra en data/imagenes_descargadas.json (URL original ->
ruta local) para no volver a descargarla si el mismo enlace reaparece.

Contenido ampliado (para la página de detalle de cada noticia): se toma el
texto más completo disponible del RSS — <content:encoded> si el feed lo trae
(algunos, como Krebs on Security, incluyen ahí el artículo casi completo), o
si no, el mismo resumen/descripción corto que ya se usa en las tarjetas. Este
texto se guarda recortado a un máximo de caracteres (ver
LIMITE_CONTENIDO_AMPLIADO) — nunca se guarda el artículo completo, ni
siquiera cuando el RSS lo trae entero; build_site.py lo recorta todavía más
al armar el resumen ampliado en español de cada página de detalle.

Salida: data/nuevas_hoy.json -> lista de ítems nuevos, listos para que
build_site.py los convierta en HTML.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import yaml

from texto import truncar

RAIZ = Path(__file__).resolve().parent.parent
FEEDS_YAML = RAIZ / "feeds.yaml"
PUBLICADAS_JSON = RAIZ / "data" / "publicadas.json"
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"
IMAGENES_LEDGER_JSON = RAIZ / "data" / "imagenes_descargadas.json"
SITE_DIR = RAIZ / "site"
IMAGENES_DIR_NOMBRE = "imagenes"

# Guayaquil = UTC-5 todo el año (mismo criterio que build_site.py para nombrar
# la carpeta del día; Ecuador no usa horario de verano).
ZONA_GUAYAQUIL = timezone(timedelta(hours=-5))

VENTANA_HORAS = 48  # tomamos ítems de las últimas 24-48h; usamos 48 para no dejar huecos
LIMITE_CONTENIDO_AMPLIADO = 4000  # tope duro al guardar; build_site.py recorta aún más al mostrar
TIMEOUT_SEGUNDOS = 20
TIMEOUT_IMAGEN_SEGUNDOS = 12
MAX_IMAGEN_BYTES = 5 * 1024 * 1024  # 5 MB: cualquier imagen más pesada se descarta
USER_AGENT = (
    "Mozilla/5.0 (compatible; PeriodicoCiberseguridadBot/1.0; "
    "+https://github.com/) NewsAggregator/1.0"
)
USER_AGENT_IMAGEN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

EXTENSIONES_POR_TIPO = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
}
EXTENSIONES_VALIDAS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif")


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


def cargar_ledger_imagenes() -> dict:
    """{url_original_de_la_imagen: ruta_local_relativa_desde_la_raiz_del_sitio}"""
    if not IMAGENES_LEDGER_JSON.exists():
        return {}
    try:
        with open(IMAGENES_LEDGER_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as ex:
        log(f"AVISO: no se pudo leer {IMAGENES_LEDGER_JSON} ({ex}); se asume vacío.")
        return {}


def guardar_ledger_imagenes(ledger: dict) -> None:
    IMAGENES_LEDGER_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(IMAGENES_LEDGER_JSON, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2, sort_keys=True)


def descargar_imagen(url: str, carpeta_fecha: str, ledger_imagenes: dict) -> str | None:
    """Descarga `url` y la guarda en site/imagenes/<carpeta_fecha>/. Devuelve la
    ruta local (relativa a la raíz del sitio, con "/" inicial) o None si la
    descarga falla por cualquier motivo — en ese caso build_site.py usa el
    ícono de categoría de respaldo, nunca se sustituye por otra imagen.

    Si `url` ya fue descargada antes (está en el ledger) y el archivo sigue
    presente en disco, se reutiliza sin volver a descargarla.
    """
    ya_conocida = ledger_imagenes.get(url)
    if ya_conocida:
        ruta_absoluta = SITE_DIR / ya_conocida.lstrip("/")
        if ruta_absoluta.exists():
            return ya_conocida
        log(f"    AVISO: {ya_conocida} estaba en el ledger pero ya no existe en disco; se vuelve a descargar.")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT_IMAGEN})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_IMAGEN_SEGUNDOS) as resp:
            if resp.status != 200:
                log(f"    AVISO: la imagen respondió HTTP {resp.status} ({url}); se usa el ícono de categoría en su lugar.")
                return None
            tipo = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not tipo.startswith("image/"):
                log(f"    AVISO: la URL de imagen no devolvió Content-Type de imagen ({tipo!r}, {url}); se usa el ícono de categoría.")
                return None
            datos = resp.read(MAX_IMAGEN_BYTES + 1)
            if len(datos) > MAX_IMAGEN_BYTES:
                log(f"    AVISO: la imagen supera {MAX_IMAGEN_BYTES // (1024 * 1024)}MB ({url}); se descarta y se usa el ícono de categoría.")
                return None
    except urllib.error.HTTPError as ex:
        log(f"    AVISO: HTTPError {ex.code} descargando imagen ({url}); se usa el ícono de categoría.")
        return None
    except urllib.error.URLError as ex:
        log(f"    AVISO: URLError ({ex.reason}) descargando imagen ({url}); se usa el ícono de categoría.")
        return None
    except TimeoutError:
        log(f"    AVISO: timeout descargando imagen ({url}); se usa el ícono de categoría.")
        return None
    except Exception as ex:  # noqa: BLE001 - una imagen caída nunca debe tumbar el proceso
        log(f"    AVISO: error inesperado ({type(ex).__name__}: {ex}) descargando imagen ({url}); se usa el ícono de categoría.")
        return None

    extension = EXTENSIONES_POR_TIPO.get(tipo)
    if not extension:
        sufijo = Path(urlparse(url).path).suffix.lower()
        extension = sufijo if sufijo in EXTENSIONES_VALIDAS else ".jpg"

    nombre_archivo = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + extension
    carpeta_destino = SITE_DIR / IMAGENES_DIR_NOMBRE / carpeta_fecha
    carpeta_destino.mkdir(parents=True, exist_ok=True)
    ruta_absoluta = carpeta_destino / nombre_archivo
    with open(ruta_absoluta, "wb") as f:
        f.write(datos)

    ruta_relativa = f"/{IMAGENES_DIR_NOMBRE}/{carpeta_fecha}/{nombre_archivo}"
    ledger_imagenes[url] = ruta_relativa
    log(f"    Imagen descargada y guardada en {ruta_relativa} ({len(datos)} bytes).")
    return ruta_relativa


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
    texto plano; build_site.py se encarga de volver a escaparlo para HTML.

    <style>/<script> se descartan ENTEROS (etiqueta y contenido) antes de
    quitar el resto de las etiquetas -- si no, su texto interno (reglas CSS,
    código JS) quedaría pegado al texto plano resultante (confirmado con el
    HTML de WordPress/Elementor de la página de prensa de la SPDP, que
    incrusta bloques <style> sueltos junto al texto de cada boletín)."""
    import html
    import re

    if not texto:
        return ""
    sin_estilos = re.sub(r"<style[^>]*>.*?</style>", " ", texto, flags=re.IGNORECASE | re.DOTALL)
    sin_estilos = re.sub(r"<script[^>]*>.*?</script>", " ", sin_estilos, flags=re.IGNORECASE | re.DOTALL)
    sin_tags = re.sub(r"<[^>]+>", " ", sin_estilos)
    decodificado = html.unescape(sin_tags)
    sin_espacios = re.sub(r"\s+", " ", decodificado).strip()
    return sin_espacios


def limpiar_html_conservando_parrafos(texto: str) -> str:
    """Como limpiar_html(), pero marca los saltos de párrafo ANTES de
    quitar el resto de las etiquetas, para que build_site.py pueda mostrar
    el contenido ampliado como varios párrafos en vez de un solo bloque de
    texto corrido.

    Antes solo <p>/<br> marcaban salto de párrafo -- si el <content:encoded>
    de una fuente arma su contenido con otra estructura (confirmado:
    INCIBE-CERT usa <table> para el resumen técnico del CVE, <ul>/<li>
    para "Listado de referencias"/"Etiquetas", y <h1>-<h6> como títulos de
    sección tipo "Descripción"/"Solución"/"Detalle", sin ningún <p>), todo
    eso quedaba pegado en una sola frase corrida y sin sentido (p.ej.
    "Identificador CVE Severidad Explotación Fabricante CVE-2026-84186
    Media No PrestaShop", que son las celdas de una tabla sin sus
    columnas). Por eso ahora:
    1. Se descartan ENTERAS las tablas y listas (<table>, <ul>, <ol>) antes
       de marcar párrafos -- son datos estructurados o metadatos
       (referencias, etiquetas), no prosa del artículo, y sin las
       columnas/viñetas originales quedan ilegibles como texto corrido.
    2. Los títulos de sección (<h1>-<h6>) también se descartan enteros (no
       solo se usan como marca de salto de párrafo) -- son etiquetas
       cortas tipo "Descripción"/"Solución", no prosa, y quedarían como
       "párrafos" sueltos de una sola palabra si se conservara su texto.
    3. Se marca salto de párrafo en más bloques, no solo <p>/<br> (también
       <div>) -- por si una fuente arma sus párrafos con otro elemento en
       vez de <p>.
    4. <style>/<script> se descartan ENTEROS (etiqueta y contenido), igual
       que en limpiar_html() -- confirmado necesario con el HTML de
       WordPress/Elementor de la página de prensa de la SPDP.
    """
    import html
    import re

    if not texto:
        return ""
    sin_estructuras = re.sub(r"<style[^>]*>.*?</style>", " ", texto, flags=re.IGNORECASE | re.DOTALL)
    sin_estructuras = re.sub(r"<script[^>]*>.*?</script>", " ", sin_estructuras, flags=re.IGNORECASE | re.DOTALL)
    sin_estructuras = re.sub(r"<table[^>]*>.*?</table>", " ", sin_estructuras, flags=re.IGNORECASE | re.DOTALL)
    sin_estructuras = re.sub(r"<(ul|ol)[^>]*>.*?</\1>", " ", sin_estructuras, flags=re.IGNORECASE | re.DOTALL)
    sin_estructuras = re.sub(r"<h[1-6][^>]*>.*?</h[1-6]>", "\n\n", sin_estructuras, flags=re.IGNORECASE | re.DOTALL)
    marcado = re.sub(
        r"</p\s*>|<br\s*/?>|</div\s*>",
        "\n\n",
        sin_estructuras,
        flags=re.IGNORECASE,
    )
    sin_tags = re.sub(r"<[^>]+>", " ", marcado)
    decodificado = html.unescape(sin_tags)
    # Una etiqueta inline pegada justo antes de un signo de puntuación (p.ej.
    # "<i>Quito, 20 de enero de 2026</i>.–") deja un espacio de sobra al
    # quitarla ("2026 .–") -- se colapsa acá, después de decodificar
    # entidades y antes de partir en líneas.
    decodificado = re.sub(r"[ \t]+([.,;:!?])", r"\1", decodificado)
    lineas = [re.sub(r"[ \t]+", " ", linea).strip() for linea in decodificado.split("\n")]
    resultado = re.sub(r"\n{3,}", "\n\n", "\n".join(lineas)).strip()
    return resultado


def extraer_contenido_ampliado(entry, extracto_ya_limpio: str) -> str:
    """Devuelve el texto más completo disponible del ítem para la página de
    detalle: <content:encoded> si el feed lo trae (algunos, como Krebs on
    Security, incluyen ahí el artículo casi completo), o si no, el mismo
    extracto corto que ya se usa en la tarjeta. Se recorta a
    LIMITE_CONTENIDO_AMPLIADO caracteres — nunca se guarda el artículo
    completo tal cual, ni siquiera cuando el RSS lo trae entero.

    El recorte usa texto.truncar() (compartido con build_site.py) en vez de
    un corte propio: así, si el RSS de origen ya trae su propio marcador de
    "leer más" (p.ej. BleepingComputer agrega ".. [...]" al final del
    extracto), se limpia acá mismo en vez de guardarlo tal cual y dejar que
    se le apile OTRO marcador más adelante, en build_site.py."""
    contenido_encoded = entry.get("content")
    texto_crudo = ""
    if contenido_encoded:
        try:
            texto_crudo = contenido_encoded[0].get("value", "") or ""
        except (AttributeError, IndexError, KeyError):
            texto_crudo = ""

    if texto_crudo:
        limpio = limpiar_html_conservando_parrafos(texto_crudo)
    else:
        limpio = extracto_ya_limpio

    return truncar(limpio, LIMITE_CONTENIDO_AMPLIADO)


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
        es_imagen = tipo.startswith("image/") or medio == "image" or url.lower().split("?")[0].endswith(EXTENSIONES_VALIDAS)
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
    # Categoría fija (opcional): para fuentes de un solo tema (p.ej. la
    # sección de Protección de Datos), en vez de inferir la categoría por
    # palabras clave del título/extracto (ver categorizar() en
    # build_site.py), se fija directamente acá. Si no se declara, el ítem
    # no lleva "categoria" y build_site.py sigue infiriéndola como siempre.
    categoria_fija = fuente.get("categoria")
    # Filtro de palabras clave (opcional): para fuentes que cubren varios
    # temas (p.ej. un despacho legal que también publica sobre protección
    # de datos, pero no solo eso) -- si se declara, un ítem SOLO se
    # publica si su título o extracto contiene alguna de estas palabras
    # (sin distinguir mayúsculas/minúsculas); si no, se descarta.
    filtro_palabras_clave = fuente.get("filtro_palabras_clave") or []
    # Ventana de antigüedad (opcional, en horas): por defecto se usa
    # VENTANA_HORAS (pensada para fuentes de alta frecuencia, como
    # ciberseguridad, que publican varias veces al día). Fuentes de menor
    # frecuencia (p.ej. blogs jurídicos de protección de datos, que
    # publican cada varias semanas) pueden fijar acá una ventana propia
    # más amplia para no perderse ítems que salieron hace más de 48h pero
    # siguen siendo "nuevos" para nuestro ledger -- la comprobación contra
    # ya_publicadas de más abajo sigue aplicando igual, así que ampliar la
    # ventana nunca reintroduce algo ya publicado.
    ventana_horas_fuente = fuente.get("ventana_horas", VENTANA_HORAS)

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

    limite = ahora_utc - timedelta(hours=ventana_horas_fuente)
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
        contenido_ampliado = extraer_contenido_ampliado(entry, extracto)
        imagen_url = extraer_imagen(entry)

        if filtro_palabras_clave:
            texto_para_filtro = f"{titulo} {extracto}".lower()
            if not any(palabra.lower() in texto_para_filtro for palabra in filtro_palabras_clave):
                log(f"  Descartado por no coincidir con el filtro de palabras clave de '{nombre}': {link}")
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
            "fecha_publicacion_original": texto_fecha_original(entry),
            "extracto_original": extracto,
            "contenido_ampliado": contenido_ampliado,
            "imagen_url": imagen_url,
        }
        if categoria_fija:
            item["categoria"] = categoria_fija
        nuevos.append(item)

    log(f"  -> {len(nuevos)} ítem(s) nuevo(s) dentro de la ventana de {ventana_horas_fuente}h desde '{nombre}'.")
    return nuevos


# ---------------------------------------------------------------------------
# Detección de "mismo hecho" cubierto por fuentes distintas (p.ej. la misma
# vulnerabilidad/ataque/filtración que The Hacker News y BleepingComputer
# publican el mismo día con títulos y URLs distintos). No usa IA -- solo
# similitud de texto determinística (difflib) y señales objetivas (CVE
# compartido, palabra clave de producto/marca compartida). Se corre una sola
# vez por corrida, sobre el lote de ítems NUEVOS de esta corrida (todos ya
# filtrados por ventana de 24-48h y por no estar publicados todavía) -- nunca
# compara contra el histórico ya publicado.
# ---------------------------------------------------------------------------

UMBRAL_SIMILITUD_TITULO = 0.62  # 0.6-0.7 sugerido; ver pruebas en el propio repo
# Piso de similitud cuando la única señal adicional es una palabra clave
# compartida (ver más abajo) -- probado contra 125 noticias reales ya
# publicadas. Varios pares genuinos (MikroTik, ScreenConnect, ClickFix,
# FalconFlank) tienen ratio de solo 0.38-0.48, así que el piso no puede ser
# demasiado alto sin perderlos; la defensa real contra falsos positivos es
# la lista PALABRAS_CLAVE_GENERICAS de abajo (ver su comentario), no este
# número.
UMBRAL_SIMILITUD_TITULO_CON_CLAVE = 0.4

RE_CVE = re.compile(r"CVE-\d{4}-\d+", re.IGNORECASE)
# Candidatos a "palabra clave principal" (marca/producto): tokens con
# mayúscula intercalada (VMware, ChatGPT) o un prefijo de 2-4 mayúsculas
# seguido de minúsculas (JFrog, ESXi) o acrónimos puros en mayúsculas de
# 2-6 letras (SMA1000). Deliberadamente NO cualquier palabra con mayúscula
# inicial sola, porque los títulos en inglés suelen venir en Title Case
# (todas las palabras importantes capitalizadas), así que eso solo
# generaría falsos positivos entre artículos no relacionados que comparten
# una palabra común como "Critical" o "New".
RE_PALABRA_CLAVE = re.compile(r"\b(?:[A-Za-z]*[a-z][A-Z][A-Za-z]*|[A-Z]{2,4}[a-z][A-Za-z]*|[A-Z0-9]{2,6})\b")
# Acrónimos/jargon genérico de ciberseguridad y empresas mencionadas TAN
# seguido (de paso, no como protagonista del hecho) que compartirlas no es
# evidencia confiable de que dos artículos hablen del mismo hecho -- nunca
# cuentan como "palabra clave" distintiva por sí solas. Casos reales
# encontrados al probar esto contra las 125 noticias ya publicadas:
# - "N-able Issues Fourth N-central Hotfix... RCE Flaw" se emparejaba por
#   error con "SonicWall SMA 1000 Zero-Days Enable Unauthenticated RCE"
#   solo por compartir el acrónimo genérico "RCE".
# - "GPT-6 Astra Scores 100%... as OpenAI Blocks PoC Exploit Requests" (un
#   hecho de IA sin relación) se emparejaba por error con "Critical
#   Langflow flaw exploited to steal OpenAI and AWS keys" solo por
#   mencionar "OpenAI" de paso los dos -- a diferencia de un título donde
#   OpenAI es el propio protagonista del hecho (esos casos, sin otra
#   palabra clave en común, quedan sin fusionar bajo este ajuste; se
#   prefiere ese falso negativo ocasional a arriesgar una fusión errónea).
PALABRAS_CLAVE_GENERICAS = {
    "RCE", "CVE", "CVES", "API", "APIS", "VPN", "VPNS", "DDOS", "IOT", "SDK",
    "URL", "SQL", "XSS", "SSRF", "MFA", "2FA", "POC", "AI", "ML", "US", "UK",
    "EU", "CEO", "CTO", "CISO", "FBI", "NSA", "GDPR", "PDF", "IOS", "APP",
    "OS", "ID", "KEV", "IT", "OPENAI", "CHATGPT",
}


def _normalizar_titulo(titulo: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", titulo.lower()).strip()


def _extraer_cves(texto: str) -> set[str]:
    return {m.upper() for m in RE_CVE.findall(texto)}


def _extraer_palabras_clave(titulo: str) -> set[str]:
    return {
        p
        for p in RE_PALABRA_CLAVE.findall(titulo)
        if len(p) >= 3 and p.upper() not in PALABRAS_CLAVE_GENERICAS
    }


def _es_mismo_hecho(a: dict, b: dict) -> bool:
    """True si `a` y `b` (de fuentes DISTINTAS) parecen cubrir el mismo
    hecho de ciberseguridad. Nunca compara ítems de la misma fuente (esos
    ya se deduplican antes, por enlace exacto)."""
    if a["fuente"] == b["fuente"]:
        return False

    ratio = difflib.SequenceMatcher(
        None, _normalizar_titulo(a["titulo"]), _normalizar_titulo(b["titulo"])
    ).ratio()
    if ratio >= UMBRAL_SIMILITUD_TITULO:
        return True

    texto_a = f"{a['titulo']} {a.get('extracto_original', '')}"
    texto_b = f"{b['titulo']} {b.get('extracto_original', '')}"
    if _extraer_cves(texto_a) & _extraer_cves(texto_b):
        return True

    # Palabra clave de producto/marca compartida: señal más débil sola (dos
    # artículos no relacionados podrían mencionar la misma marca de paso),
    # así que exige además un piso mínimo de similitud de título.
    claves_compartidas = _extraer_palabras_clave(a["titulo"]) & _extraer_palabras_clave(b["titulo"])
    if claves_compartidas and ratio >= UMBRAL_SIMILITUD_TITULO_CON_CLAVE:
        return True

    return False


def agrupar_mismo_hecho(items: list[dict]) -> list[list[dict]]:
    """Agrupa `items` en clusters de "mismo hecho" (unión de pares
    conectados por _es_mismo_hecho -- si A~B y B~C quedan los tres juntos
    aunque A y C no coincidan directo entre sí). Un ítem sin ningún par
    conectado queda solo en su propio grupo de 1."""
    n = len(items)
    padre = list(range(n))

    def encontrar(x: int) -> int:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    def unir(x: int, y: int) -> None:
        rx, ry = encontrar(x), encontrar(y)
        if rx != ry:
            padre[rx] = ry

    for i in range(n):
        for j in range(i + 1, n):
            if _es_mismo_hecho(items[i], items[j]):
                unir(i, j)

    grupos: dict[int, list[dict]] = {}
    for i, item in enumerate(items):
        grupos.setdefault(encontrar(i), []).append(item)
    return list(grupos.values())


def fusionar_grupo(grupo: list[dict]) -> dict:
    """Fusiona un grupo de ítems del MISMO hecho (fuentes distintas) en un
    solo ítem publicado: usa título/resumen/fecha de la fuente que lo
    publicó PRIMERO (fecha_publicacion_iso más antigua real -- nunca se
    inventa cuál "llegó primero"), y agrega fuentes_adicionales (nombre real
    + enlace real de cada una de las demás -- build_site.py las muestra como
    "También cubierto por: ..." en la página de detalle). Si la fuente
    principal no tiene imagen propia pero alguna de las fusionadas sí, usa
    esa en vez de perder la oportunidad de imagen real."""
    if len(grupo) == 1:
        return grupo[0]

    ordenado = sorted(grupo, key=lambda x: x["fecha_publicacion_iso"])
    principal = dict(ordenado[0])
    otras = ordenado[1:]
    principal["fuentes_adicionales"] = [
        {"nombre": it["fuente"], "enlace": it["enlace"]} for it in otras
    ]
    if not principal.get("imagen_url"):
        for it in ordenado:
            if it.get("imagen_url"):
                principal["imagen_url"] = it["imagen_url"]
                break
    return principal


def fusionar_mismo_hecho(items: list[dict]) -> list[dict]:
    """Punto de entrada: agrupa y fusiona. Devuelve una lista más corta o
    igual que `items` -- un ítem por hecho real, cada uno con
    fuentes_adicionales si más de una fuente lo cubrió."""
    grupos = agrupar_mismo_hecho(items)
    fusionados = [fusionar_grupo(g) for g in grupos]
    n_fusiones = sum(1 for g in grupos if len(g) > 1)
    if n_fusiones:
        log(f"Detectados {n_fusiones} hecho(s) cubiertos por más de una fuente -- fusionados en un solo ítem cada uno.")
        for g in grupos:
            if len(g) > 1:
                nombres = ", ".join(it["fuente"] for it in g)
                log(f"  Mismo hecho ({nombres}): {g[0]['titulo'][:80]}")
    return fusionados


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

    # Fusionar ítems de FUENTES DISTINTAS que cubren el mismo hecho (misma
    # vulnerabilidad/ataque/filtración con título y URL distintos) -- ver
    # fusionar_mismo_hecho(). Se corre acá, sobre el lote completo de esta
    # corrida, antes de descargar imágenes (para no descargar de más) y
    # antes de ordenar.
    todos_los_nuevos = fusionar_mismo_hecho(todos_los_nuevos)

    # Orden: más reciente primero
    todos_los_nuevos.sort(key=lambda x: x["fecha_publicacion_iso"], reverse=True)

    # Descargar y alojar localmente las imágenes de los ítems nuevos (en vez
    # de enlazar directo a la URL externa). Si una descarga falla, el ítem
    # queda con imagen_local=None y build_site.py usará el ícono de categoría.
    if todos_los_nuevos:
        carpeta_fecha = datetime.now(ZONA_GUAYAQUIL).strftime("%Y-%m-%d")
        ledger_imagenes = cargar_ledger_imagenes()
        log(f"Descargando imágenes de {len(todos_los_nuevos)} ítem(s) (carpeta del día: {carpeta_fecha})...")
        for item in todos_los_nuevos:
            if item.get("imagen_url"):
                item["imagen_local"] = descargar_imagen(item["imagen_url"], carpeta_fecha, ledger_imagenes)
            else:
                item["imagen_local"] = None
        guardar_ledger_imagenes(ledger_imagenes)

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
