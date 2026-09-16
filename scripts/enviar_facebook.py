#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enviar_facebook.py
---------------------
Último paso opcional del flujo diario (junto a enviar_telegram.py): publica
TODOS los afiches que generó generar_afiche.py (uno por cada noticia nueva
del día) como fotos en la Página de Facebook del sitio, vía la Graph API de
Meta (endpoint `/{page-id}/photos`).

Nunca inventa nada nuevo: cada caption sale del mismo título y link reales
que generar_afiche.py ya usó para el propio afiche (ver
data/afiches/afiches_hoy.txt) -- mismo mecanismo que enviar_telegram.py.

Credenciales: FACEBOOK_PAGE_ID y FACEBOOK_PAGE_ACCESS_TOKEN vienen SIEMPRE de
variables de entorno (GitHub Actions secrets en el workflow diario) -- nunca
hardcodeadas ni committeadas al repo. Ver README ("Afiche diario y envío a
Telegram y Facebook") para cómo obtener ambos valores.

Diseño a propósito tolerante a fallos: si faltan las credenciales, o si la
propia Graph API devuelve un error, este script LOGUEA el problema con
claridad pero nunca termina con código de error -- es un paso opcional
(notificación/difusión), nunca debe tumbar el workflow que publica el sitio
real. Si la publicación de UN afiche falla, se sigue intentando con los
demás (un error puntual de Facebook no debe cortar el resto del lote).

Uso:
    py -3 scripts/enviar_facebook.py
        # usa data/afiches/afiches_hoy.txt (lo que dejó generar_afiche.py)
    py -3 scripts/enviar_facebook.py --afiche ruta.png --titulo "..." --link "https://..."
        # para pruebas puntuales con un afiche local, título y link específicos
        # (ignora afiches_hoy.txt; manda solo este uno)
    py -3 scripts/enviar_facebook.py --url "https://.../foto.jpg" --titulo "..." --link "https://..."
        # variante de prueba que publica una imagen YA alojada en una URL
        # pública en vez de subir un archivo local -- útil para probar el
        # envío sin depender de que exista un afiche generado (ver
        # .github/workflows/prueba_facebook.yml)
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
AFICHES_DIR = RAIZ / "data" / "afiches"
AFICHES_HOY_TXT = AFICHES_DIR / "afiches_hoy.txt"

FACEBOOK_PAGE_ID = (os.environ.get("FACEBOOK_PAGE_ID") or "").strip()
FACEBOOK_PAGE_ACCESS_TOKEN = (os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN") or "").strip()

GRAPH_API_VERSION = "v26.0"
LIMITE_CAPTION = 63206  # límite real de Facebook para el caption de una foto

# Pausa entre publicaciones consecutivas cuando hay varios afiches -- igual
# criterio que enviar_telegram.py: no hay apuro (envío en segundo plano del
# workflow), y así no se acerca a ningún límite de tasa de la Graph API.
PAUSA_ENTRE_ENVIOS_SEG = 2.0


def log(mensaje: str) -> None:
    print(f"[enviar_facebook] {mensaje}", flush=True)


def _leer_afiches_hoy() -> list[tuple[Path, str, str]]:
    """Lee data/afiches/afiches_hoy.txt (grupos de 3 líneas: ruta del PNG,
    título real, link real) -- lo mismo que lee enviar_telegram.py. []
    (nunca un error) si no existe o no tiene el formato esperado."""
    if not AFICHES_HOY_TXT.exists():
        log(f"AVISO: no existe {AFICHES_HOY_TXT} (¿corriste generar_afiche.py antes?); nada que publicar.")
        return []
    lineas = AFICHES_HOY_TXT.read_text(encoding="utf-8").splitlines()
    if len(lineas) % 3 != 0:
        log(f"AVISO: {AFICHES_HOY_TXT} no tiene un múltiplo de 3 líneas (formato inesperado); nada que publicar.")
        return []

    afiches: list[tuple[Path, str, str]] = []
    for i in range(0, len(lineas), 3):
        ruta_afiche, titulo, link = Path(lineas[i]), lineas[i + 1], lineas[i + 2]
        if not ruta_afiche.exists():
            log(f"AVISO: el afiche {ruta_afiche} ya no existe; se omite.")
            continue
        afiches.append((ruta_afiche, titulo, link))
    return afiches


def _codificar_multipart(campos: dict[str, str], nombre_campo_archivo: str, ruta_archivo: Path) -> tuple[bytes, str]:
    """Arma un cuerpo multipart/form-data a mano (sin dependencias externas
    -- este repo no usa `requests`, ver enviar_telegram.py) para subir el
    archivo de la imagen junto con los campos de texto."""
    boundary = uuid.uuid4().hex
    partes: list[bytes] = []
    for nombre, valor in campos.items():
        partes.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{nombre}"\r\n\r\n{valor}\r\n'.encode("utf-8")
        )
    tipo_mime = mimetypes.guess_type(ruta_archivo.name)[0] or "application/octet-stream"
    partes.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{nombre_campo_archivo}"; '
            f'filename="{ruta_archivo.name}"\r\nContent-Type: {tipo_mime}\r\n\r\n'
        ).encode("utf-8")
    )
    partes.append(ruta_archivo.read_bytes())
    partes.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(partes), boundary


def _llamar_graph_api(datos: bytes | None, content_type: str | None, endpoint: str) -> tuple[bool, str]:
    """POST genérico a la Graph API. Devuelve (ok, texto_respuesta) --
    nunca lanza una excepción hacia quien llama."""
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{endpoint}"
    headers = {"Content-Type": content_type} if content_type else {}
    req = urllib.request.Request(url, data=datos, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return True, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        texto_error = exc.read().decode("utf-8", errors="replace")
        log(f"ERROR: la Graph API devolvió {exc.code}: {texto_error[:500]}")
        return False, texto_error
    except urllib.error.URLError as exc:
        log(f"ERROR: no se pudo conectar a la Graph API de Meta ({exc}).")
        return False, str(exc)


def publicar_foto_local(ruta_afiche: Path, caption: str) -> bool:
    """Publica `ruta_afiche` (archivo local) en la Página vía subida
    multipart. Devuelve True si Facebook confirmó la publicación (vino un
    "id" en la respuesta), False en cualquier otro caso."""
    if not FACEBOOK_PAGE_ID or not FACEBOOK_PAGE_ACCESS_TOKEN:
        log(
            "AVISO: FACEBOOK_PAGE_ID y/o FACEBOOK_PAGE_ACCESS_TOKEN no están configurados; "
            "se omite la publicación (ver README, sección 'Afiche diario y envío a Telegram y Facebook')."
        )
        return False

    if len(caption) > LIMITE_CAPTION:
        caption = caption[: LIMITE_CAPTION - 3] + "..."

    cuerpo, boundary = _codificar_multipart(
        {"caption": caption, "access_token": FACEBOOK_PAGE_ACCESS_TOKEN}, "source", ruta_afiche
    )
    ok, texto_resp = _llamar_graph_api(cuerpo, f"multipart/form-data; boundary={boundary}", f"{FACEBOOK_PAGE_ID}/photos")
    if ok and '"id"' in texto_resp:
        log(f"Afiche publicado en Facebook correctamente ({ruta_afiche.name}). Respuesta: {texto_resp[:200]}")
        return True
    log(f"ERROR: Facebook no confirmó la publicación de {ruta_afiche.name}. Respuesta: {texto_resp[:500]}")
    return False


def publicar_foto_por_url(url_imagen: str, caption: str) -> bool:
    """Variante de prueba: publica una imagen que YA está alojada en una
    URL pública (parámetro `url` de la Graph API), sin subir ningún
    archivo local -- ver docstring del módulo."""
    if not FACEBOOK_PAGE_ID or not FACEBOOK_PAGE_ACCESS_TOKEN:
        log(
            "AVISO: FACEBOOK_PAGE_ID y/o FACEBOOK_PAGE_ACCESS_TOKEN no están configurados; "
            "se omite la publicación (ver README, sección 'Afiche diario y envío a Telegram y Facebook')."
        )
        return False

    if len(caption) > LIMITE_CAPTION:
        caption = caption[: LIMITE_CAPTION - 3] + "..."

    campos = {
        "url": url_imagen,
        "caption": caption,
        "access_token": FACEBOOK_PAGE_ACCESS_TOKEN,
    }
    cuerpo = urllib.parse.urlencode(campos).encode("utf-8")
    ok, texto_resp = _llamar_graph_api(cuerpo, "application/x-www-form-urlencoded", f"{FACEBOOK_PAGE_ID}/photos")
    if ok and '"id"' in texto_resp:
        log(f"Imagen publicada en Facebook correctamente (por URL). Respuesta: {texto_resp[:200]}")
        return True
    log(f"ERROR: Facebook no confirmó la publicación por URL. Respuesta: {texto_resp[:500]}")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--afiche", type=str, default=None, help="Ruta de un afiche LOCAL a publicar (prueba puntual).")
    parser.add_argument("--url", type=str, default=None, help="URL pública de una imagen YA alojada a publicar (prueba puntual).")
    parser.add_argument("--titulo", type=str, default=None, help="Título real de la noticia (para el caption).")
    parser.add_argument("--link", type=str, default=None, help="Link real a la noticia (para el caption).")
    args = parser.parse_args()

    if args.url and args.titulo and args.link:
        caption = f"{args.titulo}\n\n{args.link}"
        log("Publicando UNA imagen de prueba por URL (--url)...")
        if publicar_foto_por_url(args.url, caption):
            log("Listo: 1/1 imagen publicada correctamente.")
        else:
            log("Listo: 0/1 imagen publicada.")
        sys.exit(0)

    if args.afiche and args.titulo and args.link:
        ruta_afiche = Path(args.afiche)
        if not ruta_afiche.exists():
            log(f"ERROR: no existe el afiche indicado: {ruta_afiche}")
            sys.exit(0)
        afiches = [(ruta_afiche, args.titulo, args.link)]
    else:
        afiches = _leer_afiches_hoy()
        if not afiches:
            sys.exit(0)  # nunca es un error -- simplemente no hay nada que publicar todavía

    log(f"Publicando {len(afiches)} afiche(s) en Facebook...")
    publicados = 0
    for i, (ruta_afiche, titulo, link) in enumerate(afiches):
        if i > 0:
            time.sleep(PAUSA_ENTRE_ENVIOS_SEG)
        caption = f"{titulo}\n\n{link}"
        if publicar_foto_local(ruta_afiche, caption):
            publicados += 1
    log(f"Listo: {publicados}/{len(afiches)} afiche(s) publicado(s) correctamente.")

    # Paso opcional (difusión): nunca termina con código de error, ni
    # siquiera si Facebook rechazó alguno de los envíos -- ver docstring
    # del módulo.
    sys.exit(0)


if __name__ == "__main__":
    main()
