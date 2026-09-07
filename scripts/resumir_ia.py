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
instalado, este script se salta por completo (no falla el proceso) y todos
los ítems quedan con resumen_ia_ok=False -- el sitio sigue funcionando
exactamente como antes de agregar este paso.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

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


def extraer_texto_completo(url: str) -> str | None:
    """Descarga `url` y extrae el texto principal del artículo (sin menús,
    publicidad, comentarios) con trafilatura. Devuelve None si trafilatura
    no está disponible, si la descarga falla, o si lo extraído es
    demasiado corto para ser el artículo real -- nunca lanza excepción."""
    try:
        import trafilatura
    except ImportError:
        log("AVISO: el paquete 'trafilatura' no está instalado; se omite la extracción de texto completo.")
        return None

    try:
        descargado = trafilatura.fetch_url(url)
        if not descargado:
            log(f"  AVISO: no se pudo descargar {url} para extraer el texto completo.")
            return None
        texto = trafilatura.extract(
            descargado,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
    except Exception as ex:  # noqa: BLE001 - una falla de extracción nunca debe tumbar el proceso
        log(f"  AVISO: error extrayendo texto completo de {url} ({type(ex).__name__}: {ex}).")
        return None

    if not texto or len(texto.strip()) < MINIMO_TEXTO_EXTRAIDO:
        log(f"  AVISO: texto extraído de {url} es insuficiente ({len(texto.strip()) if texto else 0} caracteres).")
        return None
    return texto.strip()


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
        log("AVISO: GEMINI_API_KEY no configurada; se omite el resumen con IA para todos los ítems (build_site.py usará el extracto de RSS como respaldo).")

    exitosos = 0
    for item in items:
        item["resumen_ia"] = None
        item["resumen_ia_ok"] = False

        if not GEMINI_API_KEY:
            continue

        enlace = item.get("enlace", "")
        titulo = item.get("titulo", "")
        log(f"Procesando: {titulo[:70]}...")

        texto_completo = extraer_texto_completo(enlace)
        if not texto_completo:
            continue

        resumen = resumir_con_ia(texto_completo, titulo)
        if not resumen:
            continue

        item["resumen_ia"] = resumen
        item["resumen_ia_ok"] = True
        exitosos += 1
        log(f"  OK: resumen con IA generado ({len(resumen)} caracteres).")

    guardar_nuevas(items)
    log(f"Listo: {exitosos}/{len(items)} ítem(s) con resumen de IA; el resto usará el extracto de RSS (Plan B).")


if __name__ == "__main__":
    main()
