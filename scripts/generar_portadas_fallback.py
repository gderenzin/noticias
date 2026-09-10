#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_portadas_fallback.py
----------------------------
Genera portadas dinámicas (1200x630 px, estándar web / Open Graph)
para todas las noticias que no traigan imagen propia (imagen_local es None o vacía).

Procesa:
  1. data/nuevas_hoy.json (flujo diario automático de GitHub Actions)
  2. data/proteccion_datos_recientes.json (archivo persistente de la sección)

Usa Pillow (PIL) y una paleta de degradados oscuros corporativos acordes a la
identidad de DERENZIN S.A.S. / ciberseguridad.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parent.parent
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"
PROTECCION_JSON = RAIZ / "data" / "proteccion_datos_recientes.json"
SITE_DIR = RAIZ / "site"
IMAGENES_DIR = SITE_DIR / "imagenes"

ANCHO, ALTO = 1200, 630
MARGEN_X = 80

PALETAS = {
    "proteccion_datos": {
        "bg1": (15, 23, 42),
        "bg2": (30, 41, 59),
        "acento": (56, 189, 248),  # Celeste claro
        "etiqueta": "PROTECCIÓN DE DATOS",
    },
    "vulnerabilidades": {
        "bg1": (24, 24, 27),
        "bg2": (39, 39, 42),
        "acento": (248, 113, 113),  # Rojo suave
        "etiqueta": "VULNERABILIDADES",
    },
    "ciberataques": {
        "bg1": (15, 23, 42),
        "bg2": (23, 37, 84),
        "acento": (251, 146, 60),  # Naranja
        "etiqueta": "CIBERATAQUES",
    },
    "politica": {
        "bg1": (19, 21, 26),
        "bg2": (31, 41, 55),
        "acento": (192, 132, 252),  # Violeta
        "etiqueta": "POLÍTICA Y REGULACIÓN",
    },
    "general": {
        "bg1": (15, 23, 42),
        "bg2": (30, 41, 59),
        "acento": (45, 212, 191),  # Verde esmeralda / turquesa
        "etiqueta": "CIBERSEGURIDAD",
    },
}


def log(msg: str) -> None:
    print(f"[portadas_fallback] {msg}", flush=True)


def _resolver_fuente(tamano: int, negrita: bool = False) -> ImageFont.FreeTypeFont:
    candidatos = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ]
        if negrita
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    )
    for ruta in candidatos:
        if Path(ruta).exists():
            return ImageFont.truetype(ruta, tamano)
    return ImageFont.load_default(size=tamano)


def crear_fondo_degradado(c1: tuple[int, int, int], c2: tuple[int, int, int]) -> Image.Image:
    """Genera un degradado diagonal suave de 1200x630 px."""
    img = Image.new("RGB", (ANCHO, ALTO))
    draw = ImageDraw.Draw(img)
    for y in range(ALTO):
        t = y / ALTO
        r = round(c1[0] * (1 - t) + c2[0] * t)
        g = round(c1[1] * (1 - t) + c2[1] * t)
        b = round(c1[2] * (1 - t) + c2[2] * t)
        draw.line([(0, y), (ANCHO, y)], fill=(r, g, b))
    return img


def generar_portada(titulo: str, fuente: str, categoria: str, ruta_salida: Path) -> None:
    paleta = PALETAS.get(categoria, PALETAS["general"])
    img = crear_fondo_degradado(paleta["bg1"], paleta["bg2"])
    draw = ImageDraw.Draw(img)

    # 1. Borde superior decorativo
    draw.rectangle([(0, 0), (ANCHO, 8)], fill=paleta["acento"])

    # 2. Insignia de la categoría
    font_badge = _resolver_fuente(20, negrita=True)
    etiqueta = paleta["etiqueta"]
    bbox = font_badge.getbbox(etiqueta)
    badge_ancho = (bbox[2] - bbox[0]) + 32
    draw.rounded_rectangle(
        [(MARGEN_X, 60), (MARGEN_X + badge_ancho, 98)],
        radius=6,
        fill=(15, 23, 42),
        outline=paleta["acento"],
        width=2,
    )
    draw.text((MARGEN_X + 16, 68), etiqueta, font=font_badge, fill=paleta["acento"])

    # 3. Nombre de la fuente emisora
    font_fuente = _resolver_fuente(22, negrita=False)
    texto_fuente = f"Fuente: {fuente.strip()}"
    draw.text((MARGEN_X + badge_ancho + 24, 68), texto_fuente, font=font_fuente, fill=(148, 163, 184))

    # 4. Titular de la noticia
    font_titulo = _resolver_fuente(42, negrita=True)
    lineas = textwrap.wrap(titulo.strip(), width=38)[:4]
    if len(textwrap.wrap(titulo.strip(), width=38)) > 4:
        lineas[-1] = lineas[-1].rstrip(".,") + "..."

    y_texto = 160
    for linea in lineas:
        draw.text((MARGEN_X, y_texto), linea, font=font_titulo, fill=(248, 250, 252))
        y_texto += 58

    # 5. Pie de tarjeta con marca y separación
    draw.line([(MARGEN_X, ALTO - 80), (ANCHO - MARGEN_X, ALTO - 80)], fill=(51, 65, 85), width=1)
    font_pie = _resolver_fuente(20, negrita=False)
    draw.text((MARGEN_X, ALTO - 60), "noticias.derenzin.com", font=font_pie, fill=(100, 116, 139))
    draw.text((ANCHO - MARGEN_X - 180, ALTO - 60), "DERENZIN S.A.S.", font=font_pie, fill=paleta["acento"])

    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    img.save(ruta_salida, format="JPEG", quality=90, optimize=True)


def procesar_archivo_json(ruta_json: Path, fecha_defecto: str) -> int:
    if not ruta_json.exists():
        return 0

    try:
        with open(ruta_json, "r", encoding="utf-8") as f:
            items = json.load(f)
    except Exception as e:
        log(f"Error al leer {ruta_json}: {e}")
        return 0

    modificados = 0
    for item in items:
        img_actual = item.get("imagen_local")
        if img_actual and (SITE_DIR / img_actual.lstrip("/")).exists():
            continue

        titulo = item.get("titulo_es") or item.get("titulo") or "Noticia de Ciberseguridad"
        fuente = item.get("fuente") or "Oficial"
        cat = item.get("categoria") or "proteccion_datos"
        fecha = item.get("fecha_publicacion") or fecha_defecto
        fecha_corta = fecha[:10] if len(fecha) >= 10 else fecha_defecto

        hash_id = hashlib.sha1((item.get("url") or item.get("link") or titulo).encode("utf-8")).hexdigest()[:10]
        nombre_img = f"card_{hash_id}.jpg"
        ruta_disco = SITE_DIR / "imagenes" / fecha_corta / nombre_img

        generar_portada(titulo, fuente, cat, ruta_disco)

        ruta_web = f"/imagenes/{fecha_corta}/{nombre_img}"
        item["imagen_local"] = ruta_web
        item["imagen_es_generica"] = True
        modificados += 1
        log(f"Portada creada ({cat}): {titulo[:50]}... -> {ruta_web}")

    if modificados > 0:
        with open(ruta_json, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        log(f"{ruta_json.name}: {modificados} noticias actualizadas con portada dinámica.")
    else:
        log(f"{ruta_json.name}: todas las noticias ya tenían imagen.")

    return modificados


def main() -> None:
    fecha_hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0
    total += procesar_archivo_json(NUEVAS_JSON, fecha_hoy)
    total += procesar_archivo_json(PROTECCION_JSON, fecha_hoy)
    log(f"Finalizado. Total de portadas generadas: {total}")


if __name__ == "__main__":
    main()
