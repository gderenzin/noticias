#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enviar_telegram.py
---------------------
Último paso opcional del flujo diario: envía TODOS los afiches que generó
generar_afiche.py (uno por cada noticia nueva del día) al chat de Telegram
del dueño del sitio, vía la API oficial de bots de Telegram (método
sendPhoto) -- para reenviarlos a mano al Estado de WhatsApp desde el
celular.

Nunca inventa nada nuevo: cada caption sale del mismo título y link reales
que generar_afiche.py ya usó para el propio afiche (ver
data/afiches/afiches_hoy.txt).

Credenciales: TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID vienen SIEMPRE de
variables de entorno (GitHub Actions secrets en el workflow diario) --
nunca hardcodeadas ni committeadas al repo. Ver README ("Enviar el afiche
diario a Telegram") para cómo crear el bot y obtener ambos valores.

Diseño a propósito tolerante a fallos: si faltan las credenciales, o si
la propia API de Telegram devuelve un error, este script LOGUEA el
problema con claridad pero nunca termina con código de error -- es un
paso opcional (notificación), nunca debe tumbar el workflow que publica
el sitio real. Si el envío de UN afiche falla, se sigue intentando con
los demás (un error puntual de Telegram no debe cortar el resto del
lote).

Uso:
    py -3 scripts/enviar_telegram.py
        # usa data/afiches/afiches_hoy.txt (lo que dejó generar_afiche.py)
    py -3 scripts/enviar_telegram.py --afiche ruta.png --titulo "..." --link "https://..."
        # para pruebas puntuales con un afiche/título/link específicos
        # (ignora afiches_hoy.txt; manda solo este uno)
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
AFICHES_DIR = RAIZ / "data" / "afiches"
AFICHES_HOY_TXT = AFICHES_DIR / "afiches_hoy.txt"

TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

LIMITE_CAPTION = 1024  # límite real de Telegram para el caption de sendPhoto

# Pausa entre envíos consecutivos cuando hay varios afiches -- Telegram
# limita la tasa de mensajes por chat; con esta pausa no se acerca ni de
# lejos al límite, y no hay apuro (es un envío en segundo plano del
# workflow, no una respuesta interactiva).
PAUSA_ENTRE_ENVIOS_SEG = 2.0


def log(mensaje: str) -> None:
    print(f"[enviar_telegram] {mensaje}", flush=True)


def _leer_afiches_hoy() -> list[tuple[Path, str, str]]:
    """Lee data/afiches/afiches_hoy.txt (grupos de 3 líneas: ruta del PNG,
    título real, link real) -- lo que generar_afiche.py acaba de escribir,
    uno por cada noticia nueva del día. [] si no existe o no tiene el
    formato esperado (nunca inventa un afiche/título/link alternativo)."""
    if not AFICHES_HOY_TXT.exists():
        log(f"AVISO: no existe {AFICHES_HOY_TXT} (¿corriste generar_afiche.py antes?); nada que enviar.")
        return []
    lineas = AFICHES_HOY_TXT.read_text(encoding="utf-8").splitlines()
    if len(lineas) % 3 != 0:
        log(f"AVISO: {AFICHES_HOY_TXT} no tiene un múltiplo de 3 líneas (formato inesperado); nada que enviar.")
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
        ruta_afiche = Path(args.afiche)
        if not ruta_afiche.exists():
            log(f"ERROR: no existe el afiche indicado: {ruta_afiche}")
            sys.exit(0)
        afiches = [(ruta_afiche, args.titulo, args.link)]
    else:
        afiches = _leer_afiches_hoy()
        if not afiches:
            sys.exit(0)  # nunca es un error -- simplemente no hay nada que enviar todavía

    log(f"Enviando {len(afiches)} afiche(s) a Telegram...")
    enviados = 0
    for i, (ruta_afiche, titulo, link) in enumerate(afiches):
        if i > 0:
            time.sleep(PAUSA_ENTRE_ENVIOS_SEG)
        caption = f"{titulo}\n\n{link}"
        if enviar_foto(ruta_afiche, caption):
            enviados += 1
    log(f"Listo: {enviados}/{len(afiches)} afiche(s) enviado(s) correctamente.")

    # Paso opcional (notificación): nunca termina con código de error, ni
    # siquiera si Telegram rechazó alguno de los envíos -- ver docstring
    # del módulo.
    sys.exit(0)


if __name__ == "__main__":
    main()
