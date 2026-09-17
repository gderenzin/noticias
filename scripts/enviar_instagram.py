#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enviar_instagram.py
----------------------
Paso opcional del flujo diario (igual criterio que enviar_telegram.py y
enviar_facebook.py): publica en la cuenta de Instagram (debe ser una
cuenta Profesional -- Empresa o Creador de contenido) cada afiche nuevo
que dejó generar_afiche.py, leyendo data/afiches/afiches_hoy.txt (3
líneas por afiche: ruta del PNG local, título, link) -- nunca inventa
texto nuevo.

Usa la "Instagram API with Instagram Login" (el flujo directo que Meta
lanzó en julio de 2024, ver README) -- a diferencia de la API de
Facebook, esta NO depende de que la cuenta esté vinculada a una Página
de Facebook, y usa su PROPIO token (INSTAGRAM_ACCESS_TOKEN) contra
graph.instagram.com, no graph.facebook.com. Por eso este script no
reusa FACEBOOK_PAGE_ACCESS_TOKEN.

A diferencia de Facebook, la API de publicación de contenido de
Instagram NO acepta subir el archivo directamente: exige una URL
pública de la imagen. Por eso este script no manda el PNG en sí, sino
la URL pública donde ya quedó publicado como parte del sitio (ver el
paso "Copiar los afiches al sitio" en diario.yml, que los copia a
site/imagenes/afiches/ ANTES de publicar site/ en gh-pages, así ya
están disponibles en noticias.derenzin.com/imagenes/afiches/ para
cuando corre este script).

Flujo de dos pasos de la Graph API (documentado por Meta para esta API):
  1. POST https://graph.instagram.com/{ig-user-id}/media
     (image_url, caption) -> creation_id
  2. POST https://graph.instagram.com/{ig-user-id}/media_publish
     (creation_id) -> media_id
Entre los dos pasos se consulta el estado del contenedor
(status_code) hasta que quede "FINISHED", con un puñado de reintentos
cortos -- publicar antes de que esté listo devuelve error.

Nunca hace fallar el workflow: si faltan credenciales o la Graph API
devuelve error, lo loguea y sigue (o termina con código 0).

Uso:
    py -3 scripts/enviar_instagram.py
        # publica los afiches de afiches_hoy.txt (flujo diario normal)
    py -3 scripts/enviar_instagram.py --url https://.../foto.png --titulo "..." --link "https://..."
        # prueba puntual con una sola imagen (ya debe ser pública)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402  (SITIO_BASE_URL)

RAIZ = Path(__file__).resolve().parent.parent
AFICHES_DIR = RAIZ / "data" / "afiches"
AFICHES_HOY_TXT = AFICHES_DIR / "afiches_hoy.txt"

GRAPH_INSTAGRAM_BASE = "https://graph.instagram.com"
INSTAGRAM_USER_ID = os.environ.get("INSTAGRAM_USER_ID", "").strip()
INSTAGRAM_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()

LIMITE_CAPTION = 2200  # límite real de Instagram (bastante menor que el de Facebook)
PAUSA_ENTRE_ENVIOS_SEG = 3.0
INTENTOS_STATUS = 10
PAUSA_STATUS_SEG = 2.0


def log(mensaje: str) -> None:
    print(f"[enviar_instagram] {mensaje}", flush=True)


def _url_publica_afiche(ruta_afiche: Path) -> str:
    return f"{bs.SITIO_BASE_URL}/imagenes/afiches/{ruta_afiche.name}"


def _leer_afiches_hoy() -> list[tuple[Path, str, str]]:
    """Lee data/afiches/afiches_hoy.txt (3 líneas por afiche: ruta,
    título, link) -- el mismo archivo que ya generó generar_afiche.py y
    que consume enviar_telegram.py/enviar_facebook.py. [] (con aviso,
    nunca un error) si no existe todavía."""
    if not AFICHES_HOY_TXT.exists():
        log(f"AVISO: no existe {AFICHES_HOY_TXT}; nada que publicar (¿corrió generar_afiche.py antes?).")
        return []
    lineas = AFICHES_HOY_TXT.read_text(encoding="utf-8").splitlines()
    items: list[tuple[Path, str, str]] = []
    for i in range(0, len(lineas) - 2, 3):
        ruta, titulo, link = lineas[i], lineas[i + 1], lineas[i + 2]
        items.append((Path(ruta), titulo, link))
    return items


def _llamar_graph_api(endpoint: str, datos: dict) -> tuple[bool, dict | str]:
    """POST genérico a graph.instagram.com vía form-urlencoded (esta API
    no necesita multipart -- nunca se sube un archivo, solo texto/URLs).
    Nunca lanza excepción: (False, texto_error) en cualquier falla."""
    url = f"{GRAPH_INSTAGRAM_BASE}/{endpoint}"
    cuerpo = urllib.parse.urlencode(datos).encode("utf-8")
    peticion = urllib.request.Request(url, data=cuerpo, method="POST")
    try:
        with urllib.request.urlopen(peticion, timeout=60) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return False, str(exc)


def _consultar_estado(creation_id: str) -> str | None:
    """Consulta status_code del contenedor de medios. None (nunca lanza)
    si la consulta falla -- el bucle que llama a esto simplemente
    reintenta hasta agotar los intentos."""
    url = (
        f"{GRAPH_INSTAGRAM_BASE}/{creation_id}"
        f"?fields=status_code&access_token={urllib.parse.quote(INSTAGRAM_ACCESS_TOKEN)}"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8")).get("status_code")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def publicar_foto_url(url_imagen: str, caption: str) -> bool:
    """Publica una foto en Instagram a partir de su URL PÚBLICA (no de
    un archivo local -- la API no acepta subida directa). True/False,
    nunca lanza excepción -- ver docstring del módulo."""
    caption = caption[:LIMITE_CAPTION]

    ok, resultado = _llamar_graph_api(
        f"{INSTAGRAM_USER_ID}/media",
        {"image_url": url_imagen, "caption": caption, "access_token": INSTAGRAM_ACCESS_TOKEN},
    )
    if not ok or not isinstance(resultado, dict) or "id" not in resultado:
        log(f"ERROR creando el contenedor de medios para {url_imagen}: {resultado}")
        return False
    creation_id = resultado["id"]

    for _ in range(INTENTOS_STATUS):
        estado = _consultar_estado(creation_id)
        if estado == "FINISHED":
            break
        if estado == "ERROR":
            log(f"ERROR: la Graph API marcó el contenedor {creation_id} como ERROR.")
            return False
        time.sleep(PAUSA_STATUS_SEG)
    else:
        log(f"AVISO: el contenedor {creation_id} no llegó a FINISHED tras {INTENTOS_STATUS} intentos; se intenta publicar igual.")

    ok, resultado = _llamar_graph_api(
        f"{INSTAGRAM_USER_ID}/media_publish",
        {"creation_id": creation_id, "access_token": INSTAGRAM_ACCESS_TOKEN},
    )
    if not ok or not isinstance(resultado, dict) or "id" not in resultado:
        log(f"ERROR publicando el contenedor {creation_id}: {resultado}")
        return False

    log(f"Publicado en Instagram: media_id={resultado['id']}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="URL pública de una imagen puntual (prueba manual).")
    parser.add_argument("--titulo", default="", help="Título de prueba (usar junto con --url).")
    parser.add_argument("--link", default="", help="Link de prueba (usar junto con --url).")
    args = parser.parse_args()

    if not INSTAGRAM_USER_ID or not INSTAGRAM_ACCESS_TOKEN:
        log(
            "AVISO: INSTAGRAM_USER_ID y/o INSTAGRAM_ACCESS_TOKEN no están configurados; "
            "no se publica nada en Instagram (ver README, sección de Instagram)."
        )
        sys.exit(0)

    if args.url:
        caption = f"{args.titulo}\n\n{args.link}".strip()
        publicar_foto_url(args.url, caption)
        return

    items = _leer_afiches_hoy()
    if not items:
        sys.exit(0)

    log(f"{len(items)} afiche(s) de hoy para publicar en Instagram.")
    for i, (ruta_afiche, titulo, link) in enumerate(items, start=1):
        url_imagen = _url_publica_afiche(ruta_afiche)
        caption = f"{titulo}\n\n{link}"
        publicar_foto_url(url_imagen, caption)
        if i < len(items):
            time.sleep(PAUSA_ENTRE_ENVIOS_SEG)


if __name__ == "__main__":
    main()
