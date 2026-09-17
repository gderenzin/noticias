#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
enviar_instagram_historico.py
--------------------------------
Script de migración PUNTUAL (NO forma parte del workflow diario, no se
ejecuta solo): publica en Instagram, una por una, TODAS las noticias
históricas del sitio (site/noticia/*.html) -- mismo criterio que
enviar_afiches_historicos.py (Telegram) y publicar_hoy_facebook.py
(Facebook, pero solo de un día), adaptado a las dos particularidades de
Instagram:

  1. La API de publicación de Instagram exige una URL PÚBLICA de la
     imagen (no acepta subir el archivo) -- por eso este script asume
     que los afiches ya están publicados en gh-pages bajo
     site/imagenes/afiches/<slug>.png (ver el workflow
     publicar_historico_instagram.yml, que corre
     generar_afiches_historicos.py y los publica ANTES de este paso).

  2. Instagram limita la Graph API a ~25 publicaciones cada 24h por
     cuenta. Por eso este script SIEMPRE tiene un --limite (por defecto
     20, con margen bajo el límite real) y nunca manda "todo de una" --
     para vaciar el backlog completo hay que correr este workflow varias
     veces (una por día alcanza). Además, ante el PRIMER fallo de
     publicación (sea por el límite diario o por cualquier otro motivo)
     corta el lote entero en vez de seguir intentando a ciegas con el
     resto -- no se pierde nada porque ese slug no queda marcado como
     enviado, así que la próxima corrida lo reintenta primero.

Reusa:
  - generar_afiches_historicos.extraer_articulo_detalle() para leer
    título/link tal cual quedaron publicados en cada
    site/noticia/<slug>.html (nunca inventa texto nuevo).
  - enviar_instagram.publicar_foto_url() para la publicación real
    (mismas credenciales por variable de entorno, mismo manejo
    tolerante a fallos).

Procesa en orden CRONOLÓGICO (el nombre de archivo empieza con
AAAA-MM-DD, así que el orden alfabético ya es el orden real de
publicación) -- para que el feed de Instagram quede en el mismo orden
en que salieron las noticias.

Pensado para poder cortar y volver a correr sin duplicar publicaciones:
cada envío exitoso se registra al toque en
data/afiches/enviados_instagram_historico.json (lista de slugs ya
publicados) -- si el script se corta a mitad de camino (red, límite de
la Graph API), la próxima corrida retoma donde quedó.

Uso:
    py -3 scripts/enviar_instagram_historico.py
        # publica hasta 20 noticias pendientes (las más antiguas primero)
    py -3 scripts/enviar_instagram_historico.py --limite 5
        # prueba con pocas
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402  (SITIO_BASE_URL)
import enviar_instagram as ei  # noqa: E402
import generar_afiches_historicos as gh  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
SITE_DIR = RAIZ / "site"
NOTICIA_DIR = SITE_DIR / "noticia"
AFICHES_DIR = RAIZ / "data" / "afiches"
AFICHES_PUBLICOS_DIR = SITE_DIR / "imagenes" / "afiches"
ESTADO_JSON = AFICHES_DIR / "enviados_instagram_historico.json"

LIMITE_POR_DEFECTO = 20  # margen cómodo bajo el límite real de Instagram (~25/24h)
PAUSA_ENTRE_ENVIOS_SEG = 3.0


def log(mensaje: str) -> None:
    print(f"[enviar_instagram_historico] {mensaje}", flush=True)


def _cargar_estado() -> set[str]:
    if not ESTADO_JSON.exists():
        return set()
    try:
        return set(json.loads(ESTADO_JSON.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as exc:
        log(f"AVISO: no se pudo leer {ESTADO_JSON} ({exc}); se asume que no se publicó nada todavía.")
        return set()


def _guardar_estado(enviados: set[str]) -> None:
    ESTADO_JSON.write_text(json.dumps(sorted(enviados), ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limite",
        type=int,
        default=LIMITE_POR_DEFECTO,
        help=f"Publicar como máximo N noticias en esta corrida (por defecto {LIMITE_POR_DEFECTO}).",
    )
    args = parser.parse_args()

    if not ei.INSTAGRAM_USER_ID or not ei.INSTAGRAM_ACCESS_TOKEN:
        log(
            "AVISO: INSTAGRAM_USER_ID y/o INSTAGRAM_ACCESS_TOKEN no están configurados; "
            "no se puede publicar el histórico (ver README, sección de Instagram)."
        )
        sys.exit(0)

    enviados = _cargar_estado()
    archivos = sorted(NOTICIA_DIR.glob("*.html"))  # orden cronológico real (prefijo AAAA-MM-DD)
    pendientes = [a for a in archivos if a.stem not in enviados]
    lote = pendientes[: args.limite] if args.limite else pendientes

    log(
        f"{len(archivos)} noticia(s) en total, {len(enviados)} ya publicada(s) antes, "
        f"{len(pendientes)} pendiente(s) en total -- esta corrida intenta {len(lote)}."
    )

    ok = fallidos = sin_datos = 0
    se_cortó_temprano = False
    for i, ruta_html in enumerate(lote, start=1):
        slug = ruta_html.stem

        ruta_afiche_publico = AFICHES_PUBLICOS_DIR / f"{slug}.png"
        if not ruta_afiche_publico.exists():
            log(f"AVISO: no existe {ruta_afiche_publico} (¿corrió el paso de generar/publicar afiches?); se omite.")
            sin_datos += 1
            continue

        item = gh.extraer_articulo_detalle(ruta_html)
        if item is None:
            sin_datos += 1
            continue

        url_imagen = f"{bs.SITIO_BASE_URL}/imagenes/afiches/{slug}.png"
        caption = f"{item['titulo']}\n\n{item['url_completa']}"

        publicado = ei.publicar_foto_url(url_imagen, caption)
        if publicado:
            ok += 1
            enviados.add(slug)
            _guardar_estado(enviados)  # se guarda al toque -- nunca se pierde progreso si se corta a mitad
        else:
            fallidos += 1
            log(f"ERROR: no se pudo publicar {slug}; se deja pendiente para el próximo intento.")
            # No se sigue con el resto del lote ante el PRIMER fallo: podría
            # ser el límite diario de Instagram (~25/24h, error código 9 o
            # 32), pero también cualquier otro problema puntual -- en
            # cualquier caso no tiene sentido seguir intentando a ciegas
            # con los N restantes. Nada se pierde: este slug no quedó
            # marcado como enviado, así que la próxima corrida lo reintenta
            # primero.
            se_cortó_temprano = True
            break

        if i < len(lote):
            time.sleep(PAUSA_ENTRE_ENVIOS_SEG)

    if se_cortó_temprano:
        log(
            "Se cortó el lote antes de terminar (ver el error de arriba) -- puede ser el límite diario "
            "de publicaciones de Instagram (~25/24h) u otro problema puntual. Sin problema: el estado ya "
            "quedó guardado, así que la próxima corrida retoma justo donde quedó."
        )

    log(f"Listo. Publicadas ahora: {ok}  |  Fallidas: {fallidos}  |  Sin datos/afiche: {sin_datos}")


if __name__ == "__main__":
    main()
