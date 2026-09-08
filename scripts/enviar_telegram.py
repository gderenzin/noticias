#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enviar_telegram.py
---------------------
Último paso opcional del flujo diario: envía el afiche que generó
generar_afiche.py al chat de Telegram del dueño del sitio, vía la API
oficial de bots de Telegram (método sendPhoto) -- para reenviarlo a mano
al Estado de WhatsApp desde el celular.

Nunca inventa nada nuevo: el caption sale del mismo título y link reales
que generar_afiche.py ya usó para el propio afiche (ver
data/afiches/ultimo_afiche.txt).

Credenciales: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID vienen SIEMPRE de
variables de entorno (GitHub Actions secrets en el workflow diario) --
nunca hardcodeadas ni committeadas al repo. Ver README ("Enviar el afiche
diario a Telegram") para cómo crear el bot y obtener ambos valores.

Diseño a propósito tolerante a fallos: si faltan las credenciales, o si
la propia API de Telegram devuelve un error, este script LOGUEA el
problema con claridad pero nunca termina con código de error -- es un
paso opcional (notificación), nunca debe tumbar el workflow que publica
el sitio real.

Uso:
    py -3 scripts/enviar_telegram.py
        # usa data/afiches/ultimo_afiche.txt (lo que dejó generar_afiche.py)
    py -3 scripts/enviar_telegram.py --afiche ruta.png --titulo "..." --link "https://..."
        # para pruebas puntuales con un afiche/título/link específicos
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
AFICHES_DIR = RAIZ / "data" / "afiches"
ULTIMO_AFICHE_TXT = AFICHES_DIR / "ultimo_afiche.txt"

TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

LIMITE_CAPTION = 1024  # límite real de Telegram para el caption de sendPhoto


def log(mensaje: str) -> None:
    print(f"[enviar_telegram] {mensaje}", flush=True)


def _leer_ultimo_afiche() -> tuple[Path, str, str] | None:
    """Lee data/afiches/ultimo_afiche.txt (3 líneas: ruta del PNG, título
    real, link real) -- lo que generar_afiche.py acaba de escribir. None
    si no existe o no tiene el formato esperado (nunca inventa un
    afiche/título/link alternativo)."""
    if not ULTIMO_AFICHE_TXT.exists():
        log(f"AVISO: no existe {ULTIMO_AFICHE_TXT} (¿corriste generar_afiche.py antes?); nada que enviar.")
        return None
    lineas = ULTIMO_AFICHE_TXT.read_text(encoding="utf-8").splitlines()
    if len(lineas) < 3:
        log(f"AVISO: {ULTIMO_AFICHE_TXT} no tiene el formato esperado; nada que enviar.")
        return None
    ruta_afiche, titulo, link = Path(lineas[0]), lineas[1], lineas[2]
    if not ruta_afiche.exists():
        log(f"AVISO: el afiche {ruta_afiche} ya no existe; nada que enviar.")
        return None
    return ruta_afiche, titulo, link


def _codificar_multipart(campos: dict[str, str], nombre_campo_archivo: str, ruta_archivo: Path) -> tuple[bytes, str]:
    """Arma un cuerpo multipart/form-data a mano (sin dependencias
    externas -- este repo no usa `requests`, ver el resto de los scripts)
    para subir el archivo de la imagen junto con los campos de texto; la
    API de Telegram (sendPhoto) lo requiere así para mandar el archivo
    real, no solo una URL."""
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


def enviar_foto(ruta_afiche: Path, caption: str) -> bool:
    """Envía `ruta_afiche` al chat vía sendPhoto. Devuelve True si
    Telegram confirmó el envío ("ok":true), False en cualquier otro caso
    -- nunca lanza una excepción hacia quien llama."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log(
            "AVISO: TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID no están configurados; "
            "se omite el envío (ver README, sección 'Enviar el afiche diario a Telegram')."
        )
        return False

    if len(caption) > LIMITE_CAPTION:
        caption = caption[: LIMITE_CAPTION - 3] + "..."

    cuerpo, boundary = _codificar_multipart({"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, "photo", ruta_afiche)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    req = urllib.request.Request(
        url,
        data=cuerpo,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            texto_resp = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        texto_error = exc.read().decode("utf-8", errors="replace")
        log(f"ERROR: Telegram devolvió {exc.code}: {texto_error[:500]}")
        return False
    except urllib.error.URLError as exc:
        log(f"ERROR: no se pudo conectar a la API de Telegram ({exc}).")
        return False

    if '"ok":true' in texto_resp.replace(" ", ""):
        log(f"Afiche enviado a Telegram correctamente ({ruta_afiche.name}).")
        return True

    log(f"ERROR: Telegram no confirmó el envío. Respuesta: {texto_resp[:500]}")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--afiche", type=str, default=None, help="Ruta del afiche a enviar.")
    parser.add_argument("--titulo", type=str, default=None, help="Título real de la noticia (para el caption).")
    parser.add_argument("--link", type=str, default=None, help="Link real a la noticia (para el caption).")
    args = parser.parse_args()

    if args.afiche and args.titulo and args.link:
        ruta_afiche, titulo, link = Path(args.afiche), args.titulo, args.link
        if not ruta_afiche.exists():
            log(f"ERROR: no existe el afiche indicado: {ruta_afiche}")
            sys.exit(0)
    else:
        datos = _leer_ultimo_afiche()
        if datos is None:
            sys.exit(0)  # nunca es un error -- simplemente no hay nada que enviar todavía
        ruta_afiche, titulo, link = datos

    caption = f"{titulo}\n\n{link}"
    enviar_foto(ruta_afiche, caption)
    # Paso opcional (notificación): nunca termina con código de error, ni
    # siquiera si Telegram rechazó el envío -- ver docstring del módulo.
    sys.exit(0)


if __name__ == "__main__":
    main()
