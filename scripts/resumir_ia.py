#!/usr/bin/env python3
"""
resumir_ia.py
-------------
Paso opcional entre fetch_news.py y build_site.py: para cada ítem nuevo en
data/nuevas_hoy.json, entra a la URL ORIGINAL de la fuente (item["enlace"],
ya verificado por fetch_news.py contra el dominio declarado), extrae el
texto principal del artículo con trafilatura, y le pide a un modelo barato
de Google (Gemini Flash/Flash-Lite -- capa gratuita real, a diferencia de
Anthropic que exige facturación desde el inicio) que lo resuma en español,
fiel y sin inventar.

Por qué existe: el extracto que trae el RSS de algunas fuentes viene ya
cortado por la propia fuente (WordPress agrega ".. [...]"; otras cortan su
<description> a una cantidad fija de caracteres sin avisar) — no hay forma
de "completar" ese texto sin ir a buscar el artículo real. Este script hace
eso una sola vez por noticia nueva (nunca se reprocesan las ya publicadas)
y guarda el resultado en el propio ítem para que build_site.py lo use como
la fuente principal del resumen ampliado y del resumen corto de tarjeta —
ver preparar_resumen_ampliado()/preparar_texto_mostrado() en build_site.py.

Regla de oro (igual que fetch_news.py y build_site.py): nunca se inventa
contenido. El resumen se le pide a la IA basado ÚNICAMENTE en el texto
extraído del artículo original; si la extracción o el resumen fallan por
cualquier motivo, este script no hace nada más (deja resumen_ia_ok=False) y
build_site.py cae automáticamente al extracto de RSS de siempre (ver el
"Plan B" documentado ahí).

Si GEMINI_API_KEY no está configurada, o el paquete `trafilatura` no está
instalado, se omite el resumen con IA para todos los ítems (quedan con
resumen_ia_ok=False) -- el sitio sigue funcionando exactamente como antes
de agregar este paso.

Además, ya que este script entra a la página original del artículo de
todas formas (para extraer el texto), también se aprovecha esa misma
descarga para buscar una imagen real cuando el RSS no trajo ninguna: si la
página declara <meta property="og:image"> (o twitter:image como respaldo),
se descarga esa imagen igual que se descargarían las del RSS (mismo
`descargar_imagen()`/ledger de fetch_news.py) -- nunca se genera ni se
inventa una imagen; si no se encuentra ninguna, build_site.py sigue
usando el ícono de categoría de respaldo. Esta búsqueda de imagen corre
SIEMPRE que se pueda descargar la página (incluso sin GEMINI_API_KEY),
porque no depende de la IA.
"""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_news as fn  # noqa: E402 - reusa descargar_imagen()/ledger, mismo directorio

RAIZ = Path(__file__).resolve().parent.parent
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"

GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip()
# "-lite" es la variante más barata/rápida disponible -- de sobra para un
# resumen fiel de un artículo de noticias, y entra cómodo en la capa
# gratuita de Gemini (a diferencia de Anthropic, que exige facturación desde
# el primer request). "gemini-2.5-flash-lite" dejó de estar disponible para
# cuentas nuevas (confirmado en el primer run real: HTTP 404, "no longer
# available to new users... use models/gemini-3.5-flash-lite") -- se pasó
# a esa versión.
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
GEMINI_TIMEOUT_SEGUNDOS = 45
GEMINI_MAX_OUTPUT_TOKENS = 1024

# Tope al texto que se manda a la IA (caracteres) -- ningún artículo de
# noticias legítimo necesita más que esto para un resumen fiel, y evita un
# costo/latencia impredecible si trafilatura extrae algo inesperadamente
# largo (p.ej. una página con contenido pegado de más).
LIMITE_TEXTO_PARA_IA = 12000

# Si trafilatura extrae menos que esto, se considera "no hay suficiente
# artículo real" y se cae al Plan B (extracto de RSS) en vez de resumir un
# fragmento demasiado corto como si fuera el artículo completo.
MINIMO_TEXTO_EXTRAIDO = 200

PROMPT_SISTEMA = (
    "Eres un asistente que resume artículos de noticias de ciberseguridad "
    "en español, de forma fiel y sin inventar nada. Solo usas el texto que "
    "se te entrega -- nunca agregas cifras, nombres, fechas ni conclusiones "
    "que no estén explícitamente en ese texto."
)


def log(mensaje: str) -> None:
    print(f"[resumir_ia] {mensaje}", flush=True)


def cargar_nuevas() -> list[dict]:
    if not NUEVAS_JSON.exists():
        log(f"AVISO: no existe {NUEVAS_JSON}; nada que hacer.")
        return []
    with open(NUEVAS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_nuevas(items: list[dict]) -> None:
    with open(NUEVAS_JSON, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _config_trafilatura():
    """Configuración de descarga de trafilatura, con un User-Agent de
    navegador real en vez del que trae por defecto -- ese default
    ("trafilatura/2.2.0 (+https://github.com/adbar/trafilatura)") se
    autoidentifica como bot, y algunos sitios (confirmado: incibe.es, un
    sitio .es del gobierno) lo bloquean/descartan la conexión sin
    responder (~30-45s de espera y falla) aunque el sitio funcione
    perfectamente para un navegador normal. Con este User-Agent, el mismo
    artículo de incibe.es que antes fallaba se descargó en ~4s."""
    from trafilatura.settings import use_config

    config = use_config()
    config.set(
        "DEFAULT",
        "USER_AGENTS",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    )
    # 20s alcanza de sobra con el User-Agent correcto (el artículo de
    # prueba de incibe.es tardó ~4s) -- un timeout más largo no ayuda
    # contra un bloqueo real (nunca responde de todas formas), solo
    # alarga la espera antes de caer al Plan B.
    config.set("DEFAULT", "DOWNLOAD_TIMEOUT", "20")
    return config


def descargar_pagina(url: str) -> str | None:
    """Descarga el HTML crudo de `url` (con el User-Agent real de
    _config_trafilatura, reintentando una vez). Devuelve None si
    trafilatura no está disponible o si la descarga falla -- nunca lanza
    excepción. Este HTML se reusa tanto para extraer el texto del artículo
    (extraer_texto_de_pagina) como para buscar una imagen de respaldo
    (buscar_imagen_pagina) -- una sola descarga sirve para ambas cosas."""
    try:
        import trafilatura
    except ImportError:
        log("AVISO: el paquete 'trafilatura' no está instalado; se omite la descarga de la página.")
        return None

    config = _config_trafilatura()
    for intento in (1, 2):
        try:
            descargado = trafilatura.fetch_url(url, config=config)
        except Exception as ex:  # noqa: BLE001 - una falla de descarga nunca debe tumbar el proceso
            log(f"  AVISO: error descargando {url} ({type(ex).__name__}: {ex}).")
            descargado = None
        if descargado:
            return descargado
        if intento == 1:
            log(f"  AVISO: no se pudo descargar {url} (intento 1/2); reintentando...")
    log(f"  AVISO: no se pudo descargar {url} (2 intentos).")
    return None


def extraer_texto_de_pagina(html_pagina: str) -> str | None:
    """Extrae el texto principal del artículo (sin menús, publicidad,
    comentarios) de un HTML ya descargado. Devuelve None si trafilatura no
    está disponible o si lo extraído es demasiado corto para ser el
    artículo real -- nunca lanza excepción."""
    try:
        import trafilatura
    except ImportError:
        return None
    try:
        # Sin favor_precision=True: en modo estricto, trafilatura devolvió
        # vacío para páginas con poco cuerpo de artículo y mucho "molde"
        # de plantilla alrededor (confirmado en incibe.es); el modo por
        # defecto (balanceado) extrajo el mismo artículo sin problema.
        texto = trafilatura.extract(
            html_pagina,
            config=_config_trafilatura(),
            include_comments=False,
            include_tables=False,
        )
    except Exception as ex:  # noqa: BLE001 - una falla de extracción nunca debe tumbar el proceso
        log(f"  AVISO: error extrayendo texto de la página ({type(ex).__name__}: {ex}).")
        return None

    if not texto or len(texto.strip()) < MINIMO_TEXTO_EXTRAIDO:
        log(f"  AVISO: texto extraído es insuficiente ({len(texto.strip()) if texto else 0} caracteres).")
        return None
    return texto.strip()


# Busca el content de <meta property="og:image" content="..."> (o
# name="twitter:image" como respaldo) sin importar el orden de los
# atributos dentro de la etiqueta.
_RE_OG_IMAGE = re.compile(
    r'<meta[^>]*\bproperty=["\']og:image["\'][^>]*\bcontent=["\']([^"\']+)["\']'
    r'|<meta[^>]*\bcontent=["\']([^"\']+)["\'][^>]*\bproperty=["\']og:image["\']',
    re.IGNORECASE,
)
_RE_TWITTER_IMAGE = re.compile(
    r'<meta[^>]*\bname=["\']twitter:image["\'][^>]*\bcontent=["\']([^"\']+)["\']'
    r'|<meta[^>]*\bcontent=["\']([^"\']+)["\'][^>]*\bname=["\']twitter:image["\']',
    re.IGNORECASE,
)


def buscar_imagen_pagina(html_pagina: str) -> str | None:
    """Busca <meta property="og:image"> (o twitter:image como respaldo) en
    el HTML de la página del artículo -- una imagen real que la propia
    fuente ya publicó, para cuando el RSS no trae ninguna (algunas fuentes
    sí tienen imagen destacada en la página aunque no la incluyan en su
    feed). Nunca se genera ni se inventa una imagen: si no se encuentra
    ninguna, devuelve None y quien llama sigue usando el ícono de
    categoría de respaldo."""
    for patron in (_RE_OG_IMAGE, _RE_TWITTER_IMAGE):
        m = patron.search(html_pagina)
        if m:
            url = (m.group(1) or m.group(2) or "").strip()
            if url:
                return html_lib.unescape(url)
    return None


def _log_cuerpo_error_gemini(cuerpo_bytes: bytes) -> None:
    """Registra el cuerpo de una respuesta de error de Gemini para
    diagnóstico -- la clave va en un header (x-goog-api-key), nunca en la
    URL ni en el cuerpo, así que no hay riesgo de que este log la exponga."""
    try:
        texto = cuerpo_bytes.decode("utf-8", errors="replace")
    except Exception:
        texto = "<no se pudo decodificar el cuerpo de la respuesta>"
    log(f"    Cuerpo de la respuesta de Gemini: {texto[:500]}")


def resumir_con_ia(texto_completo: str, titulo: str) -> str | None:
    """Le pide a Gemini (GEMINI_MODEL) un resumen fiel en español del texto
    ya extraído. Devuelve None (sin lanzar excepción) si no hay clave
    configurada o si la llamada falla por cualquier motivo -- nunca se
    fabrica un resumen alternativo; quien llama cae al extracto de RSS."""
    if not texto_completo or not GEMINI_API_KEY:
        return None

    prompt_usuario = f"""Redacta un resumen en español, completo y fiel, de 3 a 4 párrafos, del siguiente artículo de noticias de ciberseguridad.

Reglas estrictas:
- Básate ÚNICAMENTE en el texto del artículo que te doy más abajo. No agregues datos, cifras, nombres, fechas ni conclusiones que no estén explícitamente en ese texto.
- Parafrasea con tus propias palabras -- no copies frases textuales largas del original.
- Si el texto no alcanza para un resumen completo de 3-4 párrafos, dilo explícitamente al final de tu resumen (por ejemplo: "El artículo original no aporta más detalle sobre...") en vez de inventar contenido de relleno para completar.
- No incluyas ningún título, encabezado ni frase introductoria ("Aquí está el resumen:", etc.) -- responde solo con los párrafos del resumen, separados por una línea en blanco.

Título del artículo: {titulo}

Texto del artículo:
\"\"\"
{texto_completo[:LIMITE_TEXTO_PARA_IA]}
\"\"\"
"""

    datos = json.dumps(
        {
            "system_instruction": {"parts": [{"text": PROMPT_SISTEMA}]},
            "contents": [{"role": "user", "parts": [{"text": prompt_usuario}]}],
            "generationConfig": {
                "maxOutputTokens": GEMINI_MAX_OUTPUT_TOKENS,
                "temperature": 0.2,
            },
        }
    ).encode("utf-8")

    # La clave va en el header x-goog-api-key (no en la URL como "?key=...")
    # para que nunca quede expuesta en logs de proxies/servidores que
    # registren URLs completas.
    req = urllib.request.Request(
        GEMINI_API_URL,
        data=datos,
        method="POST",
        headers={
            "x-goog-api-key": GEMINI_API_KEY,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=GEMINI_TIMEOUT_SEGUNDOS) as resp:
            if resp.status != 200:
                log(f"  AVISO: Gemini respondió HTTP {resp.status}; se usa el extracto de RSS para este ítem.")
                _log_cuerpo_error_gemini(resp.read())
                return None
            cuerpo = json.loads(resp.read().decode("utf-8"))

        candidatos = cuerpo.get("candidates") or []
        if not candidatos:
            # Puede pasar si el filtro de seguridad de Gemini bloqueó la
            # respuesta (promptFeedback.blockReason) -- no es un error de
            # red, pero igual no hay resumen que usar.
            motivo = (cuerpo.get("promptFeedback") or {}).get("blockReason")
            log(f"  AVISO: Gemini no devolvió candidatos (blockReason={motivo}); se usa el extracto de RSS.")
            return None

        primero = candidatos[0]
        partes = ((primero.get("content") or {}).get("parts")) or []
        texto = "".join(p.get("text", "") for p in partes if isinstance(p, dict)).strip()
        if not texto:
            log(f"  AVISO: Gemini devolvió respuesta vacía (finishReason={primero.get('finishReason')}); se usa el extracto de RSS.")
            return None
        return texto
    except urllib.error.HTTPError as ex:
        log(f"  AVISO: Gemini HTTPError {ex.code}; se usa el extracto de RSS para este ítem.")
        try:
            _log_cuerpo_error_gemini(ex.read())
        except Exception:
            pass
    except urllib.error.URLError as ex:
        log(f"  AVISO: Gemini URLError ({ex.reason}); se usa el extracto de RSS para este ítem.")
    except TimeoutError:
        log("  AVISO: timeout llamando a Gemini; se usa el extracto de RSS para este ítem.")
    except (json.JSONDecodeError, KeyError, IndexError) as ex:
        log(f"  AVISO: respuesta inesperada de Gemini ({ex}); se usa el extracto de RSS para este ítem.")
    except Exception as ex:  # noqa: BLE001 - una falla de resumen nunca debe tumbar el build
        log(f"  AVISO: error inesperado llamando a Gemini ({type(ex).__name__}: {ex}); se usa el extracto de RSS.")
    return None


def main() -> None:
    items = cargar_nuevas()
    if not items:
        log("Sin ítems nuevos; nada que resumir.")
        return

    if not GEMINI_API_KEY:
        log("AVISO: GEMINI_API_KEY no configurada; se omite el resumen con IA (build_site.py usará el extracto de RSS como respaldo). Igual se busca imagen en la página si el RSS no trajo una.")

    carpeta_fecha = datetime.now(fn.ZONA_GUAYAQUIL).strftime("%Y-%m-%d")
    ledger_imagenes = fn.cargar_ledger_imagenes()
    ledger_cambio = False

    exitosos = 0
    con_imagen_extra = 0
    for item in items:
        item["resumen_ia"] = None
        item["resumen_ia_ok"] = False

        enlace = item.get("enlace", "")
        titulo = item.get("titulo", "")
        necesita_imagen = not item.get("imagen_local")

        if not GEMINI_API_KEY and not necesita_imagen:
            continue  # nada que hacer para este ítem

        log(f"Procesando: {titulo[:70]}...")
        html_pagina = descargar_pagina(enlace)
        if not html_pagina:
            continue

        if GEMINI_API_KEY:
            texto_completo = extraer_texto_de_pagina(html_pagina)
            if texto_completo:
                resumen = resumir_con_ia(texto_completo, titulo)
                if resumen:
                    item["resumen_ia"] = resumen
                    item["resumen_ia_ok"] = True
                    exitosos += 1
                    log(f"  OK: resumen con IA generado ({len(resumen)} caracteres).")

        if necesita_imagen:
            url_imagen = buscar_imagen_pagina(html_pagina)
            if url_imagen:
                ruta_local = fn.descargar_imagen(url_imagen, carpeta_fecha, ledger_imagenes)
                if ruta_local:
                    item["imagen_local"] = ruta_local
                    item["imagen_url"] = url_imagen
                    ledger_cambio = True
                    con_imagen_extra += 1
                    log(f"  OK: imagen encontrada en la página y descargada ({ruta_local}).")

    if ledger_cambio:
        fn.guardar_ledger_imagenes(ledger_imagenes)

    guardar_nuevas(items)
    log(
        f"Listo: {exitosos}/{len(items)} ítem(s) con resumen de IA; "
        f"{con_imagen_extra} con imagen encontrada en la página (el RSS no traía). "
        "El resto usará el extracto de RSS / ícono de categoría (Plan B)."
    )


if __name__ == "__main__":
    main()
