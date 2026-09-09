#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enviar_afiches_historicos.py
--------------------------------
Script de migración PUNTUAL (NO forma parte del workflow diario, no se
ejecuta solo): manda a Telegram, uno por uno, todos los afiches del
backlog que generó generar_afiches_historicos.py -- en orden
cronológico (el mismo orden de publicación real).

Reusa enviar_telegram.enviar_foto() tal cual (misma API, mismas
credenciales por variable de entorno, mismo manejo tolerante a fallos).

Pensado para poder cortar y volver a correr sin reenviar duplicados: cada
envío exitoso se registra al toque en data/afiches/enviados_historico.json
(lista de slugs ya enviados) -- si el script se corta a mitad de camino
(red, Ctrl+C, límite de Telegram), la próxima corrida retoma donde quedó.

Respeta el límite de Telegram de ~1 mensaje/segundo al mismo chat: espera
PAUSA_ENTRE_ENVIOS segundos entre cada envío, y si Telegram responde
"Too Many Requests" (429), espera el "retry_after" que indica la propia
respuesta antes de reintentar ese mismo afiche (nunca lo salta ni lo da
por enviado si no se confirmó).

Uso:
    py -3 scripts/enviar_afiches_historicos.py
    py -3 scripts/enviar_afiches_historicos.py --limite 5   # prueba con pocos
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enviar_telegram as et  # noqa: E402
import generar_afiches_historicos as gh  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
SITE_DIR = RAIZ / "site"
NOTICIA_DIR = SITE_DIR / "noticia"
AFICHES_DIR = RAIZ / "data" / "afiches"
ESTADO_JSON = AFICHES_DIR / "enviados_historico.json"

PAUSA_ENTRE_ENVIOS = 2.0  # segundos -- margen cómodo bajo el límite real de Telegram


def log(mensaje: str) -> None:
    print(f"[enviar_afiches_historicos] {mensaje}", flush=True)


def _cargar_estado() -> set[str]:
    if not ESTADO_JSON.exists():
        return set()
    try:
        return set(json.loads(ESTADO_JSON.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as exc:
        log(f"AVISO: no se pudo leer {ESTADO_JSON} ({exc}); se asume que no se envió nada todavía.")
        return set()


def _guardar_estado(enviados: set[str]) -> None:
    ESTADO_JSON.write_text(json.dumps(sorted(enviados), ensure_ascii=False, indent=2), encoding="utf-8")


def _reintentar_con_backoff(ruta_afiche: Path, caption: str, intentos_max: int = 3) -> bool:
    """Envuelve enviar_telegram.enviar_foto() para respetar el
    retry_after de Telegram si responde 429 -- enviar_foto ya maneja el
    resto de errores (credenciales ausentes, HTTP/URL error) devolviendo
    False sin lanzar excepción."""
    for intento in range(1, intentos_max + 1):
        try:
            return et.enviar_foto(ruta_afiche, caption)
        except urllib.error.HTTPError as exc:  # solo por si acaso; enviar_foto ya captura HTTPError
            if exc.code == 429:
                cuerpo = exc.read().decode("utf-8", errors="replace")
                m = re.search(r'"retry_after":(\d+)', cuerpo)
                espera = int(m.group(1)) + 1 if m else 5
                log(f"Telegram pidió esperar {espera}s (límite de envíos); reintentando...")
                time.sleep(espera)
                continue
            raise
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limite", type=int, default=None, help="Enviar como máximo N afiches (para pruebas).")
    args = parser.parse_args()

    if not et.TELEGRAM_BOT_TOKEN or not et.TELEGRAM_CHAT_ID:
        log(
            "AVISO: TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID no están configurados; "
            "no se puede enviar el backlog (ver README, sección 'Enviar el afiche diario a Telegram')."
        )
        sys.exit(0)

    enviados = _cargar_estado()
    archivos = sorted(NOTICIA_DIR.glob("*.html"))
    pendientes = [a for a in archivos if a.stem not in enviados]
    if args.limite:
        pendientes = pendientes[: args.limite]

    log(f"{len(archivos)} noticia(s) en total, {len(enviados)} ya enviada(s) antes, {len(pendientes)} pendiente(s) ahora.")

    ok = fallidos = sin_afiche = 0
    for i, ruta_html in enumerate(pendientes, start=1):
        slug = ruta_html.stem
        ruta_afiche = AFICHES_DIR / f"{slug}.png"
        if not ruta_afiche.exists():
            log(f"AVISO: no existe el afiche de {slug} (¿corriste generar_afiches_historicos.py?); se omite.")
            sin_afiche += 1
            continue

        item = gh.extraer_articulo_detalle(ruta_html)
        if item is None:
            sin_afiche += 1
            continue

        caption = f"{item['titulo']}\n\n{item['url_completa']}"
        enviado = _reintentar_con_backoff(ruta_afiche, caption)
        if enviado:
            ok += 1
            enviados.add(slug)
            _guardar_estado(enviados)  # se guarda al toque -- nunca se pierde progreso si se corta a mitad
        else:
            fallidos += 1
            log(f"ERROR: no se pudo enviar {slug}; se deja pendiente para el próximo intento.")

        if i < len(pendientes):
            time.sleep(PAUSA_ENTRE_ENVIOS)

    log(f"Listo. Enviados ahora: {ok}  |  Fallidos: {fallidos}  |  Sin afiche/formato: {sin_afiche}")


if __name__ == "__main__":
    main()
