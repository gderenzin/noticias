#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_afiches_historicos.py
--------------------------------
Script de migración PUNTUAL (NO forma parte del workflow diario, no se
ejecuta solo): genera un afiche por cada noticia YA PUBLICADA antes de
que existiera este mecanismo (ver generar_afiche.py, que desde ahora en
adelante genera uno por día para la destacada) -- para poder mandar todo
el backlog a Telegram con enviar_afiches_historicos.py.

Reusa toda la lógica de composición de generar_afiche.py (mismo diseño,
misma fuente, mismo fallback de degradado si no hay imagen real) -- lo
único que cambia es de dónde se extraen los datos: en vez de la
destacada de site/index.html, lee cada site/noticia/<slug>.html
publicada, tal cual está.

Nunca inventa nada: título, fuente y link salen de la propia página ya
publicada (el link, del <link rel="canonical"> -- el mismo que ya se usa
para SEO, no uno recalculado).

Es idempotente: si el PNG de una noticia ya existe en data/afiches/, se
omite (para poder cortar y volver a correr sin repetir trabajo).

Uso:
    py -3 scripts/generar_afiches_historicos.py
    py -3 scripts/generar_afiches_historicos.py --limite 10   # prueba con pocas
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402
import generar_afiche as gf  # noqa: E402  (reusa componer_fondo/generar_afiche/etc.)

RAIZ = Path(__file__).resolve().parent.parent
SITE_DIR = RAIZ / "site"
NOTICIA_DIR = SITE_DIR / "noticia"
AFICHES_DIR = RAIZ / "data" / "afiches"

RE_ARTICLE = re.compile(r'<article class="noticia-detalle cat-([a-z_]+)">.*?</article>', re.DOTALL)
RE_TITULO = re.compile(r'<h1 class="noticia-detalle-titulo">(.*?)</h1>', re.DOTALL)
RE_FUENTE = re.compile(r'<span class="fuente">Fuente: (?:<a[^>]*>(.*?)</a>|([^<]*))</span>')
RE_IMG = re.compile(r'<img[^>]*\bsrc="([^"]+)"')
RE_CANONICAL = re.compile(r'<link rel="canonical" href="([^"]+)">')


def log(mensaje: str) -> None:
    print(f"[generar_afiches_historicos] {mensaje}", flush=True)


def _texto_plano(fragmento: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]*>", "", fragmento)).strip()


def extraer_articulo_detalle(ruta_html: Path) -> dict | None:
    """Lee una página site/noticia/<slug>.html YA PUBLICADA y extrae sus
    datos reales -- mismo criterio que extraer_destacada_portada() en
    generar_afiche.py, pero para la página de detalle en vez de la
    destacada de portada. None si la página no tiene el formato esperado
    (se omite, nunca se inventa un reemplazo)."""
    texto = ruta_html.read_text(encoding="utf-8")

    m_canonical = RE_CANONICAL.search(texto)
    m_article = RE_ARTICLE.search(texto)
    if not m_canonical or not m_article:
        log(f"AVISO: {ruta_html.name} no tiene el formato esperado (falta canonical o artículo); se omite.")
        return None
    bloque = m_article.group(0)
    categoria = m_article.group(1)

    m_titulo = RE_TITULO.search(bloque)
    m_fuente = RE_FUENTE.search(bloque)
    if not (m_titulo and m_fuente):
        log(f"AVISO: {ruta_html.name} no tiene título o fuente reconocibles; se omite.")
        return None

    titulo = _texto_plano(m_titulo.group(1))
    fuente = _texto_plano(m_fuente.group(1) or m_fuente.group(2) or "")
    m_img = RE_IMG.search(bloque)
    imagen_local = m_img.group(1) if m_img else None
    if imagen_local and imagen_local.lower().endswith(".svg"):
        imagen_local = None

    return {
        "titulo": titulo,
        "fuente": fuente,
        "categoria": categoria,
        "imagen_local": imagen_local,
        "url_completa": m_canonical.group(1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limite", type=int, default=None, help="Procesar como máximo N páginas (para pruebas).")
    args = parser.parse_args()

    archivos = sorted(NOTICIA_DIR.glob("*.html"))
    if args.limite:
        archivos = archivos[: args.limite]
    log(f"{len(archivos)} página(s) de noticia encontradas en {NOTICIA_DIR}.")

    generados = omitidos = ya_existian = 0
    for ruta_html in archivos:
        slug = ruta_html.stem
        ruta_salida = AFICHES_DIR / f"{slug}.png"
        if ruta_salida.exists():
            ya_existian += 1
            continue

        item = extraer_articulo_detalle(ruta_html)
        if item is None:
            omitidos += 1
            continue

        gf.generar_afiche(item, ruta_salida)
        generados += 1
        if generados % 20 == 0:
            log(f"... {generados} afiches generados hasta ahora.")

    log(
        f"Listo. Generados: {generados}  |  Ya existían: {ya_existian}  |  "
        f"Omitidos (formato no reconocido): {omitidos}"
    )


if __name__ == "__main__":
    main()
