#!/usr/bin/env python3
"""
fetch_spdp.py
-------------
Fuente aparte para los boletines de prensa de la Superintendencia de
Protección de Datos Personales (SPDP, Ecuador) -- el ente rector de la
LOPDP en el país.

spdp.gob.ec NO tiene RSS funcional (confirmado: /feed/, /prensa/feed/ y los
sitemaps están vacíos o son feeds de comentarios), así que en vez de pasar
por fetch_news.py + feeds.yaml como las demás fuentes, este script:

1. Descarga el endpoint JSON público y oficial de WordPress de la página
   "Prensa" completa: https://spdp.gob.ec/wp-json/wp/v2/pages/4844
2. Separa su campo `content.rendered` (HTML con todos los "BOLETÍN DE
   PRENSA N°..." acumulados, en orden) en ítems individuales: un boletín
   por ítem, con su propio título, fecha y texto.
3. Compara contra un registro PROPIO (data/publicadas_spdp.json, separado
   del ledger general data/publicadas.json) usando el número de boletín
   como identificador estable -- es un correlativo oficial que la propia
   SPDP asigna a cada comunicado, así que es más confiable que derivar un
   identificador de un hash de texto (que cambiaría si la fuente corrige
   una errata) o de la URL (todos los boletines comparten la misma URL,
   por lo que no sirve para distinguir uno de otro).
4. Agrega los boletines nuevos a data/nuevas_hoy.json (el mismo archivo que
   escribe fetch_news.py) para que resumir_ia.py y build_site.py los
   procesen exactamente igual que cualquier otra noticia nueva.

Regla de oro: nunca se genera contenido. El resumen que se muestra es el
texto del boletín tal cual lo publica la SPDP (limpio de HTML, nunca
reescrito ni resumido por una IA -- no hace falta: ya viene en español y
razonablemente conciso). Si el boletín no menciona una fecha exacta en su
texto, se usa la fecha de última modificación de la página (`modified_gmt`
del JSON) como aproximación, y el ítem queda marcado con
`fecha_aproximada=True` para que quede registrado que no es una fecha
exacta confirmada por el boletín mismo -- nunca se inventa una fecha.

Enlace: no existe una página individual por boletín (todos comparten
https://spdp.gob.ec/prensa/), así que nunca se inventa una. Para que cada
boletín tenga de todos modos una identidad única dentro del sitio (nombre
de archivo, ruta de detalle, entrada propia en el ledger general), se usa
esa misma URL real con un fragmento (#boletin-N) agregado -- sigue siendo
la URL real y funcional de la SPDP (el fragmento no rompe la navegación:
el navegador simplemente la ignora si no hay una sección con ese id), solo
que permite que build_site.py trate cada boletín como una entidad propia
en vez de que todos compitan por el mismo archivo de detalle.

Si el endpoint falla (timeout, no responde, cambia de forma y ya no trae
content.rendered), no se rompe el workflow: se salta esta fuente, se dejan
avisos en el log, y las demás fuentes siguen su curso normal (fetch_news.py
ya corrió antes, independientemente de esto).

Uso: se corre como un paso aparte en el workflow, después de fetch_news.py
y antes de resumir_ia.py:
    py -3 scripts/fetch_news.py
    py -3 scripts/fetch_spdp.py
    py -3 scripts/resumir_ia.py
    py -3 scripts/build_site.py
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_news as fn  # noqa: E402 - reusa limpiar_html_conservando_parrafos(), mismo directorio
from texto import truncar  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"
PUBLICADAS_SPDP_JSON = RAIZ / "data" / "publicadas_spdp.json"

SPDP_JSON_URL = "https://spdp.gob.ec/wp-json/wp/v2/pages/4844"
SPDP_PRENSA_URL = "https://spdp.gob.ec/prensa/"
SPDP_NOMBRE_FUENTE = "Superintendencia de Protección de Datos Personales (SPDP)"
SPDP_DOMINIO = "spdp.gob.ec"
SPDP_CATEGORIA = "proteccion_datos"

# Logo oficial de la SPDP -- se usa como imagen fija para TODOS los
# boletines de esta fuente (no vienen de una noticia con og:image propia:
# son comunicados de texto, no hay foto real del hecho que mostrar).
# Verificado con una petición real (HTTP 200, Content-Type image/png) que
# ambas URLs responden antes de usarlas acá. Se prueba primero el logo
# cuadrado (972x971 -- mejor proporción para tarjeta que el rectangular
# del header); si no respondiera, se cae al rectangular; si ninguno de
# los dos responde, construir_item() deja imagen_local=None y
# build_site.py usa el ícono de categoría de respaldo, igual que para
# cualquier otra fuente sin imagen -- nunca se inventa una.
SPDP_LOGO_URL = "https://spdp.gob.ec/wp-content/uploads/2026/04/logospdp2.png"
SPDP_LOGO_URL_RESPALDO = "https://spdp.gob.ec/wp-content/uploads/2025/08/3-1-e1754067758126.png"
# No es una carpeta de fecha (como para las imágenes de artículos
# normales) -- es una imagen fija reutilizada por todos los boletines de
# esta fuente, así que se guarda en su propia carpeta con nombre fijo.
SPDP_LOGO_CARPETA = "spdp"

TIMEOUT_SEGUNDOS = 20
LIMITE_CONTENIDO_AMPLIADO = 4000  # mismo tope que fetch_news.py, por consistencia
USER_AGENT = (
    "Mozilla/5.0 (compatible; PeriodicoCiberseguridadBot/1.0; "
    "+https://github.com/) NewsAggregator/1.0"
)

# El bloque de cada boletín en content.rendered sigue siempre el mismo
# patrón (confirmado contra el HTML real de la página): un <div
# class="linea-centrada"> con "BOLETIN DE PRENSA N" seguido del título,
# cerrando en </div></h3>, y el cuerpo entre <h7 ...> y </h7>. "BOLETIN"
# aparece con o sin tilde según el boletín (probablemente copiado desde
# distintos documentos de origen), por eso el patrón acepta ambas formas.
RE_BOLETIN = re.compile(
    r'<div class="linea-centrada">\s*BOLET[IÍ]N\s+DE\s+PRENSA\s*N?[°º.]?\s*(\d+)'
    r'\s*(?:</br>|<br\s*/?>\s*(?:</br>)?)?\s*(.*?)</div>\s*</h3>\s*<h7[^>]*>(.*?)</h7>',
    re.IGNORECASE | re.DOTALL,
)

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
RE_FECHA_BOLETIN = re.compile(
    r"Quito,\s*(\d{1,2})\s+de\s+([A-Za-záéíóúñ]+)\s+de\s+(\d{4})", re.IGNORECASE
)

# Boilerplate de cierre que varios boletines repiten al final (confirmado
# contra el HTML real): una línea de firma "Más información: <url>", o un
# botón de descarga de PDF ("Descarga ... aquí: Ver documento") cuyo enlace
# de todos modos se pierde al limpiar el HTML. Ninguno de los dos es texto
# narrativo del boletín -- se recortan para no dejarlos como última
# "oración" (lo que además hacía que fuente_parece_incompleta() marcara
# estos boletines como si vinieran cortados, sin estarlo: su fuente NO es
# un RSS y el texto SÍ está completo, solo que termina en una firma/botón,
# no en un punto).
_RE_PIE_MAS_INFO = re.compile(r"m[áa]s\s+informaci[óo]n\s*:\s*\S+", re.IGNORECASE)
_RE_PIE_DESCARGA = re.compile(r"descarga\b.{0,80}aqu[íi]\s*:?(?:\s*ver\s+documento)?", re.IGNORECASE | re.DOTALL)


def _quitar_pie_boilerplate(texto: str) -> str:
    parrafos = texto.split("\n\n")
    while parrafos:
        ultimo = parrafos[-1].strip()
        if _RE_PIE_MAS_INFO.fullmatch(ultimo) or _RE_PIE_DESCARGA.fullmatch(ultimo):
            parrafos.pop()
            continue
        break
    return "\n\n".join(parrafos).strip()


def log(mensaje: str) -> None:
    print(f"[fetch_spdp] {mensaje}", flush=True)


def cargar_publicadas_spdp() -> dict:
    if not PUBLICADAS_SPDP_JSON.exists():
        return {"boletines": {}}
    try:
        with open(PUBLICADAS_SPDP_JSON, "r", encoding="utf-8") as f:
            datos = json.load(f)
        datos.setdefault("boletines", {})
        return datos
    except (json.JSONDecodeError, OSError) as ex:
        log(f"AVISO: no se pudo leer {PUBLICADAS_SPDP_JSON} ({ex}); se asume vacío.")
        return {"boletines": {}}


def guardar_publicadas_spdp(datos: dict) -> None:
    PUBLICADAS_SPDP_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(PUBLICADAS_SPDP_JSON, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2, sort_keys=True)


def cargar_nuevas_existentes() -> list[dict]:
    """Lo que fetch_news.py ya haya escrito en este mismo run (o una lista
    vacía si no existe / está corrupto) -- se le agregan los boletines
    nuevos de la SPDP sin pisar lo que ya trajo fetch_news.py."""
    if not NUEVAS_JSON.exists():
        return []
    try:
        with open(NUEVAS_JSON, "r", encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, list) else []
    except (json.JSONDecodeError, OSError) as ex:
        log(f"AVISO: no se pudo leer {NUEVAS_JSON} ({ex}); se asume vacío.")
        return []


def guardar_nuevas(items: list[dict]) -> None:
    NUEVAS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(NUEVAS_JSON, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def descargar_json_spdp() -> dict | None:
    """Descarga y parsea el endpoint JSON. Devuelve None (sin lanzar
    excepción) ante cualquier falla -- timeout, HTTP de error, JSON
    inválido, o si la forma de la respuesta cambió y ya no trae
    content.rendered -- para que el workflow nunca se rompa por esta
    fuente en particular."""
    req = urllib.request.Request(SPDP_JSON_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEGUNDOS) as resp:
            if resp.status != 200:
                log(f"AVISO: el endpoint de la SPDP respondió HTTP {resp.status}; se salta esta fuente.")
                return None
            datos = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        log(f"AVISO: HTTPError {ex.code} descargando el endpoint de la SPDP; se salta esta fuente.")
        return None
    except urllib.error.URLError as ex:
        log(f"AVISO: URLError ({ex.reason}) descargando el endpoint de la SPDP; se salta esta fuente.")
        return None
    except TimeoutError:
        log("AVISO: timeout descargando el endpoint de la SPDP; se salta esta fuente.")
        return None
    except json.JSONDecodeError as ex:
        log(f"AVISO: el endpoint de la SPDP no devolvió JSON válido ({ex}); se salta esta fuente.")
        return None
    except Exception as ex:  # noqa: BLE001 - una falla acá nunca debe tumbar el workflow
        log(f"AVISO: error inesperado ({type(ex).__name__}: {ex}) descargando el endpoint de la SPDP; se salta esta fuente.")
        return None

    if "content" not in datos or "rendered" not in datos.get("content", {}):
        log("AVISO: la respuesta del endpoint de la SPDP ya no trae content.rendered (¿cambió de forma?); se salta esta fuente.")
        return None

    return datos


def extraer_boletines_crudos(contenido_rendered: str) -> list[tuple[int, str, str]]:
    """Devuelve [(numero, titulo_html, cuerpo_html), ...] tal cual aparecen
    en la página, sin limpiar todavía -- ver RE_BOLETIN."""
    resultado = []
    for m in RE_BOLETIN.finditer(contenido_rendered):
        numero_str, titulo_html, cuerpo_html = m.groups()
        try:
            numero = int(numero_str)
        except ValueError:
            continue
        resultado.append((numero, titulo_html, cuerpo_html))
    return resultado


def limpiar_titulo(titulo_html: str) -> str:
    import html as html_lib
    sin_tags = re.sub(r"<[^>]+>", " ", titulo_html)
    decodificado = html_lib.unescape(sin_tags)
    return re.sub(r"\s+", " ", decodificado).strip()


def parsear_fecha_boletin(cuerpo_limpio: str) -> datetime | None:
    """Busca "Quito, D de MES de AAAA" en el texto ya limpio del boletín.
    Devuelve None si no aparece (boletín sin fecha explícita en el texto) --
    quien llama decide el respaldo (modified_gmt de la página), nunca se
    inventa una fecha."""
    m = RE_FECHA_BOLETIN.search(cuerpo_limpio)
    if not m:
        return None
    dia_str, mes_texto, anio_str = m.groups()
    mes = MESES_ES.get(mes_texto.lower())
    if not mes:
        return None
    try:
        dia = int(dia_str)
        anio = int(anio_str)
        # Sin hora exacta en el texto -- se fija el mediodía hora de
        # Guayaquil (UTC-5, sin horario de verano) como convención neutra,
        # igual de válida que cualquier otra hora del mismo día.
        return datetime(anio, mes, dia, 12, 0, 0, tzinfo=fn.ZONA_GUAYAQUIL)
    except ValueError:
        return None


def obtener_logo_spdp(ledger_imagenes: dict) -> tuple[str, str] | None:
    """Descarga (o reusa del ledger, si ya se descargó antes) el logo
    oficial de la SPDP para usarlo como imagen de todos los boletines.
    Prueba primero SPDP_LOGO_URL; si falla, SPDP_LOGO_URL_RESPALDO.
    Devuelve (ruta_local, url_usada), o None si ninguna de las dos
    responde -- nunca lanza excepción."""
    for url in (SPDP_LOGO_URL, SPDP_LOGO_URL_RESPALDO):
        ruta_local = fn.descargar_imagen(url, SPDP_LOGO_CARPETA, ledger_imagenes)
        if ruta_local:
            return ruta_local, url
    log("AVISO: no se pudo descargar ninguno de los dos logos conocidos de la SPDP; los boletines usarán el ícono de categoría.")
    return None


def construir_item(
    numero: int, titulo_html: str, cuerpo_html: str, modified_gmt: str, logo_spdp: tuple[str, str] | None
) -> dict | None:
    titulo = limpiar_titulo(titulo_html)
    cuerpo_limpio = _quitar_pie_boilerplate(fn.limpiar_html_conservando_parrafos(cuerpo_html))
    if not titulo or not cuerpo_limpio:
        log(f"  AVISO: boletín {numero} no tiene título o cuerpo utilizable tras limpiar el HTML; se omite.")
        return None

    fecha = parsear_fecha_boletin(cuerpo_limpio)
    fecha_aproximada = False
    fecha_original_texto = ""
    m_fecha = RE_FECHA_BOLETIN.search(cuerpo_limpio)
    if m_fecha:
        fecha_original_texto = m_fecha.group(0)
    if fecha is None:
        fecha_aproximada = True
        try:
            fecha = datetime.fromisoformat(modified_gmt).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            log(f"  AVISO: boletín {numero} no trae fecha en su texto y modified_gmt ({modified_gmt!r}) no se pudo interpretar; se omite.")
            return None
        fecha_original_texto = (
            "Fecha exacta no indicada en el boletín; se usa la fecha de última "
            "modificación de la página de prensa de la SPDP como aproximación."
        )

    extracto_original = re.sub(r"\s+", " ", cuerpo_limpio).strip()
    contenido_ampliado = truncar(cuerpo_limpio, LIMITE_CONTENIDO_AMPLIADO)
    enlace = f"{SPDP_PRENSA_URL}#boletin-{numero}"
    imagen_local, imagen_url = logo_spdp if logo_spdp else (None, None)

    return {
        "titulo": titulo,
        "fuente": SPDP_NOMBRE_FUENTE,
        "idioma": "es",
        "url_fuente_feed": SPDP_JSON_URL,
        "dominio_fuente": SPDP_DOMINIO,
        "enlace": enlace,
        "guid": f"spdp-boletin-{numero}",
        "fecha_publicacion_iso": fecha.isoformat(),
        "fecha_publicacion_original": fecha_original_texto,
        "fecha_aproximada": fecha_aproximada,
        "extracto_original": extracto_original,
        "contenido_ampliado": contenido_ampliado,
        "imagen_url": imagen_url,
        "imagen_local": imagen_local,
        "categoria": SPDP_CATEGORIA,
        # No tiene sentido que resumir_ia.py entre a SPDP_PRENSA_URL: esa
        # página trae TODOS los boletines juntos, no solo este, así que
        # trafilatura extraería una mezcla de varios boletines en vez del
        # texto de este en particular -- un resumen de IA sobre eso sería
        # infiel. El texto real de este boletín ya viene íntegro arriba
        # (contenido_ampliado), sin necesidad de IA. Por la misma razón
        # tampoco tiene sentido buscar ahí una og:image para este boletín
        # específico.
        "omitir_resumen_ia": True,
    }


def main() -> None:
    log("Inicio de ejecución.")
    respuesta = descargar_json_spdp()
    if respuesta is None:
        log("Sin datos de la SPDP en este run; no se agrega nada. Fin de ejecución.")
        return

    modified_gmt = respuesta.get("modified_gmt", "")
    contenido_rendered = respuesta["content"]["rendered"]
    boletines_crudos = extraer_boletines_crudos(contenido_rendered)
    log(f"Boletines encontrados en la página: {len(boletines_crudos)}.")
    if not boletines_crudos:
        log("AVISO: no se reconoció ningún 'BOLETÍN DE PRENSA N°' en content.rendered (¿cambió el formato de la página?); se salta esta fuente.")
        return

    publicadas = cargar_publicadas_spdp()
    ya_vistos = publicadas["boletines"]

    pendientes = [b for b in boletines_crudos if str(b[0]) not in ya_vistos]
    if not pendientes:
        log("Sin boletines nuevos (todos los presentes en la página ya estaban en el registro). Fin de ejecución.")
        return

    # El logo es una imagen fija reutilizada por todos los boletines de
    # esta fuente (no una por boletín) -- se descarga (o se reusa del
    # ledger) una sola vez por corrida, solo si hay algo nuevo que
    # publicar.
    ledger_imagenes = fn.cargar_ledger_imagenes()
    ledger_antes = dict(ledger_imagenes)
    logo_spdp = obtener_logo_spdp(ledger_imagenes)
    if ledger_imagenes != ledger_antes:
        fn.guardar_ledger_imagenes(ledger_imagenes)

    nuevos: list[dict] = []
    for numero, titulo_html, cuerpo_html in pendientes:
        item = construir_item(numero, titulo_html, cuerpo_html, modified_gmt, logo_spdp)
        if item is None:
            continue
        nuevos.append(item)
        ya_vistos[str(numero)] = {
            "titulo": item["titulo"],
            "fecha_publicacion_iso": item["fecha_publicacion_iso"],
            "fecha_agregada_iso": datetime.now(timezone.utc).isoformat(),
        }

    if not nuevos:
        log("Ningún boletín pendiente se pudo procesar (ver avisos arriba). Fin de ejecución.")
        return

    existentes = cargar_nuevas_existentes()
    combinados = existentes + nuevos
    combinados.sort(key=lambda x: x["fecha_publicacion_iso"], reverse=True)
    guardar_nuevas(combinados)
    guardar_publicadas_spdp(publicadas)

    log(f"Listo: {len(nuevos)} boletín(es) nuevo(s) de la SPDP agregado(s) a {NUEVAS_JSON.name} ({len(combinados)} ítem(s) en total en el archivo).")
    for item in nuevos:
        aprox = " (fecha aproximada)" if item["fecha_aproximada"] else ""
        log(f"  - {item['titulo'][:70]}...{aprox}")


if __name__ == "__main__":
    main()
