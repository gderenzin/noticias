#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publicar_hoy_facebook.py
---------------------------
Script de migración PUNTUAL (NO forma parte del workflow diario, no se
ejecuta solo): publica en la Página de Facebook, uno por uno, TODAS las
noticias ya publicadas hoy en el sitio -- pensado para el día en que se
activó recién enviar_facebook.py, cuando las corridas de la mañana ya
habían publicado noticias en el sitio (y en Telegram) sin poder mandarlas
a Facebook todavía porque faltaban los secretos.

A diferencia de generar_afiches_historicos.py / enviar_afiches_historicos.py
(que procesan TODO el archivo histórico del sitio), este script se limita
a un solo día -- por defecto, "hoy" en America/Guayaquil -- para no volver
a publicar años de noticias viejas de una sola vez.

Reusa:
  - generar_afiches_historicos.extraer_articulo_detalle() para leer
    título/fuente/categoría/imagen/link tal cual quedaron publicados en
    cada site/noticia/<slug>.html (nunca inventa texto nuevo).
  - generar_afiche.generar_afiche() para componer el mismo afiche vertical
    (1080x1920) que ya usa el flujo diario -- se genera solo si todavía no
    existe en data/afiches/ (idempotente).
  - enviar_facebook.publicar_foto_local() para la publicación real en la
    Graph API, con las mismas credenciales por variable de entorno
    (FACEBOOK_PAGE_ID / FACEBOOK_PAGE_ACCESS_TOKEN).

Pensado para poder cortar y volver a correr sin duplicar publicaciones:
cada envío exitoso se registra al toque en
data/afiches/enviados_facebook_<fecha>.json (lista de slugs ya
publicados ese día) -- si el script se corta a mitad de camino (red,
límite de la Graph API), la próxima corrida retoma donde quedó.

Uso:
    py -3 scripts/publicar_hoy_facebook.py
        # publica las noticias de HOY (fecha de Guayaquil) que falten
    py -3 scripts/publicar_hoy_facebook.py --fecha 2026-09-16
        # un día puntual distinto de hoy
    py -3 scripts/publicar_hoy_facebook.py --limite 5
        # prueba con pocas antes de mandar el lote completo
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402  (ZONA_GUAYAQUIL, datetime)
import enviar_facebook as ef  # noqa: E402
import generar_afiche as gf  # noqa: E402
import generar_afiches_historicos as gh  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
SITE_DIR = RAIZ / "site"
NOTICIA_DIR = SITE_DIR / "noticia"
AFICHES_DIR = RAIZ / "data" / "afiches"

PAUSA_ENTRE_ENVIOS_SEG = 3.0  # margen cómodo bajo cualquier límite de la Graph API


def log(mensaje: str) -> None:
    print(f"[publicar_hoy_facebook] {mensaje}", flush=True)


def _estado_json(fecha: str) -> Path:
    return AFICHES_DIR / f"enviados_facebook_{fecha}.json"


def _cargar_estado(fecha: str) -> set[str]:
    ruta = _estado_json(fecha)
    if not ruta.exists():
        return set()
    try:
        return set(json.loads(ruta.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as exc:
        log(f"AVISO: no se pudo leer {ruta} ({exc}); se asume que no se publicó nada todavía.")
        return set()


def _guardar_estado(fecha: str, enviados: set[str]) -> None:
    _estado_json(fecha).write_text(json.dumps(sorted(enviados), ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fecha", type=str, default=None, help="Fecha AAAA-MM-DD a publicar (por defecto: hoy en America/Guayaquil).")
    parser.add_argument("--limite", type=int, default=None, help="Publicar como máximo N noticias (para pruebas).")
    args = parser.parse_args()

    fecha = args.fecha or bs.datetime.now(bs.ZONA_GUAYAQUIL).strftime("%Y-%m-%d")

    if not ef.FACEBOOK_PAGE_ID or not ef.FACEBOOK_PAGE_ACCESS_TOKEN:
        log(
            "AVISO: FACEBOOK_PAGE_ID y/o FACEBOOK_PAGE_ACCESS_TOKEN no están configurados; "
            "no se puede publicar el lote de hoy (ver README, sección 'Afiche diario y envío a Telegram y Facebook')."
        )
        sys.exit(0)

    archivos = sorted(NOTICIA_DIR.glob(f"{fecha}-*.html"))
    log(f"Fecha objetivo: {fecha}. {len(archivos)} noticia(s) encontradas con ese prefijo de archivo.")

    enviados = _cargar_estado(fecha)
    pendientes = [a for a in archivos if a.stem not in enviados]
    if args.limite:
        pendientes = pendientes[: args.limite]

    log(f"{len(enviados)} ya publicada(s) antes (si se re-corrió este script), {len(pendientes)} pendiente(s) ahora.")

    ok = fallidos = sin_datos = 0
    for i, ruta_html in enumerate(pendientes, start=1):
        slug = ruta_html.stem

        item = gh.extraer_articulo_detalle(ruta_html)
        if item is None:
            sin_datos += 1
            continue

        ruta_afiche = AFICHES_DIR / f"{slug}.png"
        if not ruta_afiche.exists():
            gf.generar_afiche(item, ruta_afiche)

        caption = f"{item['titulo']}\n\n{item['url_completa']}"
        publicado = ef.publicar_foto_local(ruta_afiche, caption)
        if publicado:
            ok += 1
            enviados.add(slug)
            _guardar_estado(fecha, enviados)  # se guarda al toque -- nunca se pierde progreso si se corta a mitad
        else:
            fallidos += 1
            log(f"ERROR: no se pudo publicar {slug}; se deja pendiente para el próximo intento.")

        if i < len(pendientes):
            time.sleep(PAUSA_ENTRE_ENVIOS_SEG)

    log(f"Listo. Publicadas ahora: {ok}  |  Fallidas: {fallidos}  |  Sin datos reconocibles: {sin_datos}")


if __name__ == "__main__":
    main()
