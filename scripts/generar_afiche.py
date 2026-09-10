#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generar_afiche.py
-------------------
Último paso opcional del flujo diario: genera un afiche vertical (1080x1920,
formato Historia/Estado) de CADA noticia nueva del día -- las mismas que
fetch_news.py/fetch_spdp.py dejaron en data/nuevas_hoy.json y que
build_site.py acaba de publicar como páginas de detalle en
site/noticia/ -- para reenviar a mano al Estado de WhatsApp (ver
enviar_telegram.py, que las manda al chat de Telegram del dueño del sitio
vía la API de bots, una foto por noticia).

Nunca inventa nada: todo el texto (título, fuente, imagen, link) sale
leyendo directo la página de detalle YA PUBLICADA de cada noticia en
site/noticia/ -- el mismo mecanismo de "leer de la página ya publicada"
usado en otros scripts puntuales de este repo. El nombre de archivo de esa
página se recalcula con la MISMA función que usó build_site.py
(bs.nombre_archivo_noticia) para no tener que adivinar ni volver a
parsear site/index.html. Si el título no se pudo traducir, el afiche
muestra exactamente lo que ya muestra el sitio (nunca traduce ni resume
por su cuenta). Si una noticia no tiene imagen real (ícono de categoría /
SVG), su afiche usa un fondo degradado de la marca en vez de inventar o
forzar una imagen -- Pillow no puede abrir SVG de todas formas.

Uso:
    py -3 scripts/generar_afiche.py
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402  (reusa CATEGORIAS/GENERICO/SITIO_BASE_URL/nombre_archivo_noticia, fuente única de verdad)

RAIZ = Path(__file__).resolve().parent.parent
SITE_DIR = RAIZ / "site"
NOTICIA_DIR = SITE_DIR / "noticia"
AFICHES_DIR = RAIZ / "data" / "afiches"
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"
AFICHES_HOY_TXT = AFICHES_DIR / "afiches_hoy.txt"

ANCHO, ALTO = 1080, 1920
MARGEN = 64

# Extraídos de la página de detalle de cada noticia (site/noticia/*.html,
# plantilla render_pagina_noticia() de build_site.py) -- NO de site/index.html,
# para no depender de cuántas tarjetas entran en la portada ni de si una
# noticia quedó o no como "destacada": cada noticia nueva SIEMPRE tiene su
# propia página de detalle, generada incondicionalmente por build_site.py.
RE_ARTICLE_DETALLE = re.compile(r'<article class="noticia-detalle cat-([a-z_]+)">(.*?)</article>', re.DOTALL)
RE_TITULO_DETALLE = re.compile(r'<h1 class="noticia-detalle-titulo">(.*?)</h1>', re.DOTALL)
# "Fuente: NombreFuente" o "Fuente: <a href=...>NombreFuente</a>" (los
# análisis originales envuelven la fuente en un link; las demás noticias no).
RE_FUENTE_DETALLE = re.compile(r'<span class="fuente">Fuente: (?:<a[^>]*>)?([^<]+)')
RE_FIGURA_DETALLE = re.compile(r'<figure class="noticia-imagen[^"]*">(.*?)</figure>', re.DOTALL)
RE_IMG = re.compile(r'<img[^>]*\bsrc="([^"]+)"')


def log(mensaje: str) -> None:
    print(f"[generar_afiche] {mensaje}", flush=True)


def _texto_plano(fragmento: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]*>", "", fragmento)).strip()


def cargar_items_nuevos_hoy() -> list[dict]:
    """Lee data/nuevas_hoy.json -- el mismo archivo transitorio que
    fetch_news.py/fetch_spdp.py llenaron y que build_site.py acaba de
    consumir en esta misma corrida. [] (nunca un error) si no existe o
    viene vacío -- un día sin noticias nuevas no genera ningún afiche."""
    if not NUEVAS_JSON.exists():
        log(f"AVISO: no existe {NUEVAS_JSON}; nada que generar.")
        return []
    try:
        with open(NUEVAS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log(f"AVISO: no se pudo leer {NUEVAS_JSON} ({exc}); nada que generar.")
        return []


def extraer_item_publicado(item_nuevo: dict, fecha_str: str) -> dict | None:
    """Dado un ítem de data/nuevas_hoy.json, ubica y lee la página de
    detalle que build_site.py YA publicó para él en esta misma corrida
    (mismo cálculo de nombre de archivo que usó build_site.py -- ver
    bs.nombre_archivo_noticia) y devuelve el título/fuente/imagen/categoría
    reales tal cual quedaron publicados ahí. None (con aviso, nunca un
    error) si la página esperada no existe o no tiene el formato
    esperado -- nunca inventa un reemplazo."""
    nombre_archivo = bs.nombre_archivo_noticia(item_nuevo, fecha_str)
    ruta_archivo = NOTICIA_DIR / nombre_archivo
    if not ruta_archivo.exists():
        log(f"AVISO: no existe {ruta_archivo} (¿build_site.py corrió antes?); se omite esta noticia.")
        return None
    texto = ruta_archivo.read_text(encoding="utf-8")

    m_article = RE_ARTICLE_DETALLE.search(texto)
    if not m_article:
        log(f"AVISO: {ruta_archivo} no tiene el formato esperado; se omite esta noticia.")
        return None
    categoria = m_article.group(1)
    bloque = m_article.group(2)

    m_titulo = RE_TITULO_DETALLE.search(bloque)
    m_fuente = RE_FUENTE_DETALLE.search(bloque)
    if not (m_titulo and m_fuente):
        log(f"AVISO: {ruta_archivo} no tiene título o fuente reconocibles; se omite esta noticia.")
        return None

    titulo = _texto_plano(m_titulo.group(1))
    fuente = _texto_plano(m_fuente.group(1))

    imagen_local = None
    m_figura = RE_FIGURA_DETALLE.search(bloque)
    if m_figura:
        m_img = RE_IMG.search(m_figura.group(1))
        if m_img:
            imagen_local = m_img.group(1)
    # El ícono de respaldo es SVG -- Pillow no lo puede abrir como foto de
    # fondo, así que se trata igual que "sin imagen real" (fondo degradado
    # de marca, ver componer_fondo()).
    if imagen_local and imagen_local.lower().endswith(".svg"):
        imagen_local = None

    ruta_noticia = f"/noticia/{nombre_archivo}"
    return {
        "titulo": titulo,
        "fuente": fuente,
        "categoria": categoria,
        "ruta_noticia": ruta_noticia,
        "imagen_local": imagen_local,
        "url_completa": bs.url_absoluta(ruta_noticia),
    }


def _resolver_fuente_ttf(negrita: bool) -> str | None:
    """Busca una fuente TTF instalada en el sistema -- Ubuntu (GitHub
    Actions), Windows (pruebas locales) o macOS, en ese orden. No se
    empaqueta ninguna fuente propia en el repo para no agregar binarios ni
    depender de una descarga en cada corrida; el resultado es legible en
    los tres casos aunque no sea pixel-a-pixel la misma tipografía
    (Inter) que el sitio web."""
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
            return ruta
    log("AVISO: no se encontró ninguna fuente TTF del sistema; se usa la fuente bitmap interna de Pillow (más chica/menos prolija).")
    return None


def _fuente(tamano: int, negrita: bool = False) -> ImageFont.FreeTypeFont:
    ruta = _resolver_fuente_ttf(negrita)
    if ruta:
        return ImageFont.truetype(ruta, tamano)
    return ImageFont.load_default(size=tamano)


def componer_fondo(imagen_local: str | None, color_categoria: str) -> Image.Image:
    """Foto real de la noticia, recortada/centrada a 1080x1920 (formato
    Historia) -- o, si no hay imagen real, un degradado liso con el color
    de la categoría (nunca una foto inventada ni de stock)."""
    if imagen_local:
        ruta_archivo = SITE_DIR / imagen_local.lstrip("/")
        if ruta_archivo.exists():
            try:
                foto = Image.open(ruta_archivo).convert("RGB")
                # Recorte tipo "cover": escala hasta cubrir 1080x1920 por el
                # lado más chico, después recorta el sobrante centrado.
                escala = max(ANCHO / foto.width, ALTO / foto.height)
                nuevo_ancho, nuevo_alto = round(foto.width * escala), round(foto.height * escala)
                foto = foto.resize((nuevo_ancho, nuevo_alto), Image.LANCZOS)
                x0 = (nuevo_ancho - ANCHO) // 2
                y0 = (nuevo_alto - ALTO) // 2
                return foto.crop((x0, y0, x0 + ANCHO, y0 + ALTO))
            except Exception as exc:  # noqa: BLE001 -- cualquier fallo de imagen cae al fondo de respaldo
                log(f"AVISO: no se pudo abrir/recortar {ruta_archivo} ({exc}); se usa fondo de respaldo.")

    # Fondo de respaldo: degradado vertical liso del color de la categoría
    # hacia un tono casi negro -- nunca un color inventado, sale del mismo
    # CATEGORIAS de build_site.py que usa el resto del sitio.
    fondo = Image.new("RGB", (ANCHO, ALTO))
    r0, g0, b0 = tuple(int(color_categoria.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    for y in range(ALTO):
        t = y / ALTO
        r = round(r0 * (1 - t) * 0.55)
        g = round(g0 * (1 - t) * 0.55)
        b = round(b0 * (1 - t) * 0.55)
        ImageDraw.Draw(fondo).line([(0, y), (ANCHO, y)], fill=(r, g, b))
    return fondo


def aplicar_overlay_oscuro(fondo: Image.Image) -> Image.Image:
    """Overlay oscuro semitransparente (más fuerte hacia abajo, donde va
    el texto) para que el texto siempre quede legible sobre la foto real,
    sin importar qué tan clara sea."""
    overlay = Image.new("L", (1, ALTO), color=0)
    for y in range(ALTO):
        t = y / ALTO
        # 45% arriba (para que el logo se lea) -> 88% en el tercio inferior
        # (donde va el título/fuente/link).
        opacidad = 0.45 + 0.43 * min(1.0, t / 0.62)
        overlay.putpixel((0, y), round(opacidad * 255))
    overlay = overlay.resize((ANCHO, ALTO))
    negro = Image.new("RGB", (ANCHO, ALTO), (6, 9, 15))
    return Image.composite(negro, fondo, overlay)


def envolver_texto(draw: ImageDraw.ImageDraw, texto: str, fuente: ImageFont.FreeTypeFont, ancho_max: int) -> list[str]:
    """Envuelve `texto` a líneas que quepan en `ancho_max` píxeles con
    `fuente` -- envoltura real medida con Pillow, no una estimación de
    caracteres por línea."""
    palabras = texto.split()
    lineas: list[str] = []
    actual = ""
    for palabra in palabras:
        candidata = f"{actual} {palabra}".strip()
        ancho_candidata = draw.textbbox((0, 0), candidata, font=fuente)[2]
        if ancho_candidata <= ancho_max or not actual:
            actual = candidata
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


def truncar_para_ancho(draw: ImageDraw.ImageDraw, texto: str, fuente: ImageFont.FreeTypeFont, ancho_max: int) -> str:
    """Trunca `texto` (con "…" al final) hasta que quepa en `ancho_max`
    píxeles en una sola línea."""
    if draw.textbbox((0, 0), texto, font=fuente)[2] <= ancho_max:
        return texto
    recortado = texto
    while recortado and draw.textbbox((0, 0), recortado + "…", font=fuente)[2] > ancho_max:
        recortado = recortado[:-1]
    return recortado + "…"


def envolver_url(draw: ImageDraw.ImageDraw, url: str, fuente: ImageFont.FreeTypeFont, ancho_max: int, maximo_lineas: int = 2) -> list[str]:
    """Envuelve una URL (sin espacios, así que envolver_texto() no tiene
    dónde cortar -- una URL larga se salía del afiche en la primera
    prueba real, ver el commit) partiendo después de cada "/" en vez de
    después de cada palabra. Si aun así no entra en `maximo_lineas`, la
    última línea se trunca con "…"."""
    segmentos = re.findall(r"[^/]*/|[^/]+$", url)  # cada segmento conserva su "/" final
    lineas: list[str] = []
    actual = ""
    for segmento in segmentos:
        candidata = actual + segmento
        if draw.textbbox((0, 0), candidata, font=fuente)[2] <= ancho_max or not actual:
            actual = candidata
        else:
            lineas.append(actual)
            actual = segmento
    if actual:
        lineas.append(actual)

    if len(lineas) > maximo_lineas:
        lineas = lineas[:maximo_lineas]
    if len(lineas) == maximo_lineas:
        lineas[-1] = truncar_para_ancho(draw, lineas[-1], fuente, ancho_max)
    return lineas


def generar_afiche(item: dict, ruta_salida: Path) -> Path:
    info_categoria = bs.CATEGORIAS.get(item["categoria"], bs.GENERICO)
    color_categoria = info_categoria["color"]

    fondo = componer_fondo(item["imagen_local"], color_categoria)
    imagen = aplicar_overlay_oscuro(fondo).convert("RGB")
    draw = ImageDraw.Draw(imagen)
    ancho_texto = ANCHO - 2 * MARGEN

    # ---- Marca arriba ----
    fuente_marca = _fuente(40, negrita=True)
    draw.text((MARGEN, 70), "NOTICIAS DE CIBERSEGURIDAD", font=fuente_marca, fill=(245, 247, 250))
    fuente_byline = _fuente(26)
    draw.text((MARGEN, 122), "Un proyecto de DERENZIN S.A.S.", font=fuente_byline, fill=(200, 206, 216))

    # ---- Badge de categoría ----
    fuente_badge = _fuente(30, negrita=True)
    etiqueta_badge = info_categoria["etiqueta"].upper()
    bbox_badge = draw.textbbox((0, 0), etiqueta_badge, font=fuente_badge)
    pad_badge_x, pad_badge_y = 24, 14
    badge_y0 = 1120
    badge_ancho = (bbox_badge[2] - bbox_badge[0]) + 2 * pad_badge_x
    badge_alto = (bbox_badge[3] - bbox_badge[1]) + 2 * pad_badge_y
    r, g, b = tuple(int(color_categoria.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    draw.rounded_rectangle(
        [MARGEN, badge_y0, MARGEN + badge_ancho, badge_y0 + badge_alto], radius=10, fill=(r, g, b)
    )
    draw.text((MARGEN + pad_badge_x, badge_y0 + pad_badge_y - bbox_badge[1]), etiqueta_badge, font=fuente_badge, fill=(255, 255, 255))

    # ---- Título (grande, envuelto, tamaño automático según largo real) ----
    y_titulo = badge_y0 + badge_alto + 40
    tamano_titulo = 66 if len(item["titulo"]) <= 90 else 54
    fuente_titulo = _fuente(tamano_titulo, negrita=True)
    lineas_titulo = envolver_texto(draw, item["titulo"], fuente_titulo, ancho_texto)
    alto_linea_titulo = int(tamano_titulo * 1.22)
    for linea in lineas_titulo:
        draw.text((MARGEN, y_titulo), linea, font=fuente_titulo, fill=(255, 255, 255))
        y_titulo += alto_linea_titulo

    # ---- Fuente real ----
    y_fuente = y_titulo + 28
    fuente_fuente = _fuente(32, negrita=True)
    draw.text((MARGEN, y_fuente), f"Fuente: {item['fuente']}", font=fuente_fuente, fill=(210, 216, 224))

    # ---- Línea divisoria + link ----
    y_linea = ALTO - 150
    draw.line([(MARGEN, y_linea), (ANCHO - MARGEN, y_linea)], fill=(255, 255, 255, 60), width=2)
    fuente_link = _fuente(28)
    lineas_link = envolver_url(draw, item["url_completa"], fuente_link, ancho_texto)
    y_link = y_linea + 24
    for linea in lineas_link:
        draw.text((MARGEN, y_link), linea, font=fuente_link, fill=(150, 220, 235))
        y_link += 36

    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    imagen.save(ruta_salida, "PNG")
    return ruta_salida


def _slug_desde_ruta(ruta_noticia: str) -> str:
    return Path(ruta_noticia).stem


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    items_nuevos = cargar_items_nuevos_hoy()
    if not items_nuevos:
        # Nunca es un error -- simplemente no hay nada que generar todavía
        # (p.ej. día sin noticias nuevas, o sitio recién inicializado). El
        # workflow no debe fallar por esto.
        sys.exit(0)

    fecha_str = bs.datetime.now(bs.ZONA_GUAYAQUIL).strftime("%Y-%m-%d")

    generados: list[tuple[Path, str, str]] = []
    for item_nuevo in items_nuevos:
        item = extraer_item_publicado(item_nuevo, fecha_str)
        if item is None:
            continue
        ruta_salida = AFICHES_DIR / f"{_slug_desde_ruta(item['ruta_noticia'])}.png"
        ruta_final = generar_afiche(item, ruta_salida)
        log(f"Afiche generado: {ruta_final}")
        log(f"  Título: {item['titulo'][:80]}")
        log(f"  Fuente: {item['fuente']}")
        log(f"  Link: {item['url_completa']}")
        generados.append((ruta_final, item["titulo"], item["url_completa"]))

    if not generados:
        sys.exit(0)

    log(f"Total de afiches generados hoy: {len(generados)}.")

    # Para que enviar_telegram.py (paso siguiente del workflow) sepa qué
    # mandar sin tener que re-parsear nada -- un archivo de texto simple (3
    # líneas por afiche: ruta del PNG, título, link), no JSON, para no
    # acoplar ambos scripts a un esquema.
    AFICHES_DIR.mkdir(parents=True, exist_ok=True)
    with open(AFICHES_HOY_TXT, "w", encoding="utf-8") as f:
        for ruta_final, titulo, url_completa in generados:
            f.write(f"{ruta_final}\n{titulo}\n{url_completa}\n")


if __name__ == "__main__":
    main()
