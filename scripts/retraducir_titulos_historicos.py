#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retraducir_titulos_historicos.py
------------------------------------
Script de migración PUNTUAL, pensado para correr en GitHub Actions vía
workflow_dispatch (ver .github/workflows/retraducir_titulos_historicos.yml)
-- NUNCA con la clave de DeepL puesta a mano en este entorno local.

Reintenta la traducción del título de las noticias YA PUBLICADAS cuyo
título quedó en inglés porque la traducción falló en su momento -- el
único criterio para elegirlas es que su propia página ya publicada tenga
la nota "No se pudo traducir el título automáticamente" que
build_site.py le puso entonces (nunca la categoría ni ningún otro campo:
así se evita tocar Protección de Datos, que nunca pasa por esta rama
-- ver AVISO_TRANSPARENCIA_IA en build_site.py para el mismo criterio
aplicado en la otra migración de este repo).

Si la traducción tiene éxito esta vez, actualiza TODO lo que depende del
título en esa página de detalle -- reusando las mismas funciones de
build_site.py que generan esas piezas para noticias nuevas (nunca
reconstruye el HTML a mano por su cuenta):
  - <title>, meta OG/Twitter (render_meta_seo)
  - JSON-LD (render_json_ld_noticia)
  - <h1>
  - alt de la imagen principal (si es una foto real)
  - botones de compartir (render_botones_compartir)
  - nota de idioma (colapsa a una sola, ya que al traducirse el título
    su nota vuelve a coincidir con la del resumen ampliado)
También busca y actualiza la tarjeta de esa noticia en su página de
archivo del día (dondequiera que haya quedado, sin asumir la fecha por
el nombre del archivo).

Si la traducción vuelve a fallar, la página queda exactamente como
estaba -- nunca se inventa una traducción a mano.

Uso (en CI):
    DEEPL_API_KEY=... python scripts/retraducir_titulos_historicos.py
"""

from __future__ import annotations

import html as html_lib
import json as json_lib
import re
import sys
from html import escape
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
NOTICIA_DIR = RAIZ / "site" / "noticia"
ARCHIVO_DIR = RAIZ / "site" / "archivo"
FEEDS_YAML = RAIZ / "feeds.yaml"

NOTA_FALLO_TITULO = "No se pudo traducir el título automáticamente (se muestra el original)."

RE_H1 = re.compile(r'<h1 class="noticia-detalle-titulo">(.*?)</h1>', re.DOTALL)
RE_TITLE_TAG = re.compile(r'<title>.*? — Noticias de Ciberseguridad</title>')
RE_META_BLOCK = re.compile(
    r'  <meta name="description".*?<meta name="twitter:image" content="[^"]*">', re.DOTALL
)
RE_CANONICAL = re.compile(r'<link rel="canonical" href="([^"]+)">')
RE_OG_IMAGE = re.compile(r'<meta property="og:image" content="([^"]*)">')
RE_OG_DESC = re.compile(r'<meta property="og:description" content="([^"]*)">')
RE_DESCRIPTION = re.compile(r'<meta name="description" content="([^"]*)">')
RE_JSONLD = re.compile(r'  <script type="application/ld\+json">(.*?)</script>', re.DOTALL)
RE_COMPARTIR_BLOCK = re.compile(r'      <div class="compartir">.*?\n      </div>\n', re.DOTALL)
RE_FUENTE_LINK = re.compile(r'<span class="fuente">Fuente: <a[^>]*>(.*?)</a></span>')
RE_CATEGORIA_CLASE = re.compile(r'<article class="noticia-detalle cat-([a-z_]+)">')
RE_IMG_PRINCIPAL = re.compile(r'(<img src="(?:/imagenes/[^"]+)" alt=")(.*?)("[^>]*>)')
RE_NOTAS_BLOQUE = re.compile(r'(?:<span class="idioma-nota">.*?</span>)+')


def log(mensaje: str) -> None:
    print(f"[retraducir_titulos_historicos] {mensaje}", flush=True)


def _texto_plano(fragmento: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]*>", "", fragmento)).strip()


def _cargar_idioma_por_fuente() -> dict[str, str]:
    datos = yaml.safe_load(FEEDS_YAML.read_text(encoding="utf-8"))
    return {f["nombre"]: f.get("idioma", "en") for f in datos.get("fuentes", [])}


def _actualizar_archivo_relacionado(ruta_noticia: str, titulo_viejo_html: str, titulo_nuevo_html: str) -> bool:
    """Busca la tarjeta de esta noticia en site/archivo/*.html (sin asumir
    en qué día quedó archivada) y le actualiza el título y el alt de la
    imagen, si la encuentra. Nunca inventa: si no encuentra ninguna
    coincidencia, no toca nada."""
    enlace = f'href="{ruta_noticia}"'
    tocado = False
    for archivo in sorted(ARCHIVO_DIR.glob("*.html")):
        if archivo.name == "index.html":
            continue
        texto = archivo.read_text(encoding="utf-8")
        if enlace not in texto or titulo_viejo_html not in texto:
            continue
        nuevo_texto = texto.replace(f">{titulo_viejo_html}<", f">{titulo_nuevo_html}<")
        nuevo_texto = nuevo_texto.replace(f'alt="{titulo_viejo_html}"', f'alt="{titulo_nuevo_html}"')
        if nuevo_texto != texto:
            archivo.write_text(nuevo_texto, encoding="utf-8")
            log(f"  también actualizado en site/archivo/{archivo.name}")
            tocado = True
    return tocado


def procesar_archivo(archivo: Path, idioma_por_fuente: dict[str, str]) -> str:
    """'traducido', 'sin_cambio' (no aplica o falló de nuevo) u 'omitido'
    (formato no reconocido)."""
    texto = archivo.read_text(encoding="utf-8")
    if NOTA_FALLO_TITULO not in texto:
        return "sin_cambio"

    m_h1 = RE_H1.search(texto)
    m_fuente = RE_FUENTE_LINK.search(texto)
    m_canonical = RE_CANONICAL.search(texto)
    m_categoria = RE_CATEGORIA_CLASE.search(texto)
    if not (m_h1 and m_fuente and m_canonical and m_categoria):
        log(f"AVISO: {archivo.name} no tiene el formato esperado; se omite.")
        return "omitido"

    titulo_viejo_html = m_h1.group(1)
    titulo_original = _texto_plano(titulo_viejo_html)
    fuente = _texto_plano(m_fuente.group(1))
    categoria = m_categoria.group(1)
    ruta_noticia = m_canonical.group(1).replace(bs.SITIO_BASE_URL, "")
    idioma = idioma_por_fuente.get(fuente, "en")

    titulo_traducido = bs.traducir_deepl(titulo_original, idioma)
    if not titulo_traducido:
        log(f"Sigue sin poder traducirse: {titulo_original[:70]}...")
        return "sin_cambio"

    titulo_nuevo_html = escape(titulo_traducido)
    log(f"Traducido: «{titulo_original[:60]}...» -> «{titulo_traducido[:60]}...»")

    # 1. <title>
    texto = RE_TITLE_TAG.sub(
        f"<title>{titulo_nuevo_html} — Noticias de Ciberseguridad</title>".replace("\\", "\\\\"),
        texto,
        count=1,
    )

    # 2. Bloque de <meta> (description/canonical/OG/Twitter) -- se
    #    regenera con la MISMA función que usa build_site.py, reusando la
    #    descripción/imagen ya publicadas (no dependen del título, no se
    #    recalculan).
    m_desc = RE_DESCRIPTION.search(texto)
    m_og_desc = RE_OG_DESC.search(texto)
    m_og_img = RE_OG_IMAGE.search(texto)
    descripcion = html_lib.unescape(m_desc.group(1)) if m_desc else ""
    descripcion_social = html_lib.unescape(m_og_desc.group(1)) if m_og_desc else descripcion
    imagen_pagina = (m_og_img.group(1) if m_og_img else "").replace(bs.SITIO_BASE_URL, "")
    nuevo_bloque_meta = bs.render_meta_seo(
        f"{titulo_traducido} — Noticias de Ciberseguridad",
        descripcion,
        ruta_noticia,
        imagen_pagina,
        tipo_og="article",
        descripcion_social=descripcion_social,
    )
    texto = RE_META_BLOCK.sub(nuevo_bloque_meta.replace("\\", "\\\\"), texto, count=1)

    # 3. JSON-LD
    m_jsonld = RE_JSONLD.search(texto)
    if m_jsonld:
        datos_jsonld = json_lib.loads(m_jsonld.group(1))
        imagenes = datos_jsonld.get("image") or []
        imagen_local_jsonld = imagenes[0].replace(bs.SITIO_BASE_URL, "") if imagenes else None
        item_jsonld = {
            "fecha_publicacion_iso": datos_jsonld["datePublished"],
            "imagen_local": imagen_local_jsonld,
            "fuente": fuente,
        }
        nuevo_jsonld = bs.render_json_ld_noticia(item_jsonld, titulo_traducido, ruta_noticia, categoria)
        texto = RE_JSONLD.sub(nuevo_jsonld.replace("\\", "\\\\"), texto, count=1)

    # 4. <h1>
    texto = RE_H1.sub(
        f'<h1 class="noticia-detalle-titulo">{titulo_nuevo_html}</h1>'.replace("\\", "\\\\"), texto, count=1
    )

    # 5. alt de la imagen principal (solo aplica si es una foto real)
    texto = RE_IMG_PRINCIPAL.sub(
        lambda m: f"{m.group(1)}{titulo_nuevo_html}{m.group(3)}", texto, count=1
    )

    # 6. Botones de compartir (se regeneran enteros con la misma función)
    texto = RE_COMPARTIR_BLOCK.sub(
        bs.render_botones_compartir(titulo_traducido, ruta_noticia).replace("\\", "\\\\"), texto, count=1
    )

    # 7. Nota de idioma: ya no hace falta decir "no se pudo traducir el
    #    título" -- colapsa a la misma nota que ya compartía con el
    #    resumen ampliado (antes se mostraban 2 veces por la diferencia).
    texto = RE_NOTAS_BLOQUE.sub(
        f'<span class="idioma-nota">{escape(bs.NOTA_RESUMEN_IA)}</span>'.replace("\\", "\\\\"), texto, count=1
    )

    archivo.write_text(texto, encoding="utf-8")
    _actualizar_archivo_relacionado(ruta_noticia, titulo_viejo_html, titulo_nuevo_html)
    return "traducido"


def main() -> None:
    if not bs.DEEPL_API_KEY:
        log("AVISO: DEEPL_API_KEY no configurada; no se puede reintentar ninguna traducción.")
        sys.exit(0)

    idioma_por_fuente = _cargar_idioma_por_fuente()
    archivos = sorted(NOTICIA_DIR.glob("*.html"))
    log(f"{len(archivos)} página(s) de noticia encontradas; reintentando las que fallaron al traducir el título...")

    conteo = {"traducido": 0, "sin_cambio": 0, "omitido": 0}
    for archivo in archivos:
        try:
            resultado = procesar_archivo(archivo, idioma_por_fuente)
        except Exception as exc:  # noqa: BLE001 -- nunca tumbar el resto de la corrida por un archivo
            log(f"ERROR inesperado procesando {archivo.name}: {exc}; se omite sin tocarlo.")
            resultado = "omitido"
        conteo[resultado] += 1

    log(
        f"Listo. Traducidos ahora: {conteo['traducido']}  |  "
        f"Sin cambio (no aplica o sigue fallando): {conteo['sin_cambio']}  |  "
        f"Omitidos (error/formato): {conteo['omitido']}"
    )


if __name__ == "__main__":
    main()
