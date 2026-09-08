#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reprocesar_ia_septiembre.py
-----------------------------
Script de reprocesamiento PUNTUAL (no forma parte del workflow diario):
las 89 noticias del backfill del 1-4 de septiembre de 2026 (ver
backfill_septiembre.py) se publicaron sin resumen de IA (Gemini) porque esa
corrida no tenía GEMINI_API_KEY disponible localmente -- cayeron en Plan B
(extracto de RSS, sin traducir), a diferencia del 6-7 de septiembre, que sí
vinieron del workflow real con las claves configuradas.

Este script reprocesa esas 89 páginas YA PUBLICADAS con el mismo mecanismo
real que usa resumir_ia.py para las noticias del día a día: entra a la URL
original de cada una (ya verificada, guardada en la propia página
publicada), extrae el texto completo con trafilatura, y le pide a Gemini un
resumen fiel en español. Nunca inventa nada -- si Gemini falla para algún
ítem, esa página se deja tal cual estaba (con su contenido real de Plan B),
nunca se reemplaza por texto inventado.

Alcance: sin DEEPL_API_KEY (no disponible para esta corrida), el TÍTULO de
los artículos en inglés se queda en inglés -- exactamente el mismo
comportamiento que ya tiene preparar_texto_mostrado() cuando hay resumen de
IA pero falla la traducción del título: se muestra con una nota clara
("No se pudo traducir el título automáticamente"), nunca se inventa una
traducción.

Reconstruye los datos de cada ítem (título, fuente, enlace, fecha,
categoría, imagen) leyéndolos directo de la página ya publicada -- no
depende de data/nuevas_hoy.json (que ya se sobreescribió varias veces desde
el backfill original). La categoría se preserva exactamente como está
mostrada hoy (nunca se re-infiere por palabras clave, para no arriesgar un
cambio de categoría). El contenido_ampliado/extracto_original actuales
(Plan B, real) se reconstruyen también, como respaldo por si Gemini falla
para algún ítem puntual.

Regenera, para cada uno de los 4 días (1, 2, 3 y 4 de septiembre): todas
las páginas de detalle de ese día, y el archivo completo del día
(site/archivo/AAAA-MM-DD.html) con las tarjetas actualizadas -- se
reconstruye entero a partir de la lista corregida de ítems de ese día, ya
que cada uno de estos 4 días es una edición autocontenida (nadie corrió el
workflow real esos días, no hay nada más que preservar). No toca
site/index.html (ya refleja el 7 de septiembre, la edición real más
reciente) ni el ledger data/publicadas.json (las URLs/rutas no cambian).

Uso:
    GEMINI_API_KEY=<clave> py -3 scripts/reprocesar_ia_septiembre.py
    GEMINI_API_KEY=<clave> py -3 scripts/reprocesar_ia_septiembre.py --dry-run
    GEMINI_API_KEY=<clave> py -3 scripts/reprocesar_ia_septiembre.py --solo-fallidos   # segunda pasada, solo reintenta los que fallaron
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402
import fetch_news as fn  # noqa: E402
import resumir_ia as ri  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
NOTICIA_DIR = RAIZ / "site" / "noticia"
ARCHIVO_DIR = RAIZ / "site" / "archivo"

FECHAS = ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"]

RE_TITULO = re.compile(r'<h1 class="noticia-detalle-titulo">(.*?)</h1>', re.DOTALL)
RE_EYEBROW = re.compile(r'<span class="eyebrow-categoria">([^<]*)</span>')
RE_FUENTE_ENLACE = re.compile(r'<span class="fuente">Fuente: <a href="([^"]+)"[^>]*>([^<]*)</a></span>')
RE_FECHA_JSONLD = re.compile(r'"datePublished":\s*"([^"]+)"')
RE_IMG_REAL = re.compile(
    r'noticia-imagen--destacada">\s*<span class="categoria-badge">[^<]*</span>\s*<img src="(/imagenes/[^"]+)"'
)
RE_CUERPO = re.compile(r'noticia-detalle-cuerpo">\n(.*?)\n\s*</div>', re.DOTALL)
RE_P = re.compile(r"<p>(.*?)</p>")

ETIQUETA_A_SLUG = {info["etiqueta"]: slug for slug, info in bs.CATEGORIAS.items()}
ETIQUETA_A_SLUG[bs.GENERICO["etiqueta"]] = "generico"


def log(mensaje: str) -> None:
    print(f"[reprocesar_ia_septiembre] {mensaje}", flush=True)


def _texto_plano(html_fragmento: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]*>", "", html_fragmento)).strip()


def leer_item_publicado(archivo: Path) -> dict | None:
    """Reconstruye los campos de un ítem a partir de su página de detalle YA
    publicada -- no depende de data/nuevas_hoy.json."""
    texto = archivo.read_text(encoding="utf-8")

    m_titulo = RE_TITULO.search(texto)
    m_eyebrow = RE_EYEBROW.search(texto)
    m_fuente_enlace = RE_FUENTE_ENLACE.search(texto)
    m_fecha = RE_FECHA_JSONLD.search(texto)
    m_cuerpo = RE_CUERPO.search(texto)
    if not (m_titulo and m_eyebrow and m_fuente_enlace and m_fecha and m_cuerpo):
        log(f"  AVISO: no se pudieron extraer todos los campos esperados de {archivo.name}; se omite.")
        return None

    titulo = _texto_plano(m_titulo.group(1))
    etiqueta = _texto_plano(m_eyebrow.group(1))
    categoria = ETIQUETA_A_SLUG.get(etiqueta, "generico")
    enlace = html_lib.unescape(m_fuente_enlace.group(1))
    fuente_nombre = _texto_plano(m_fuente_enlace.group(2))
    fecha_iso = m_fecha.group(1)

    m_img = RE_IMG_REAL.search(texto)
    imagen_local = m_img.group(1) if m_img else None

    parrafos_actuales = [_texto_plano(p) for p in RE_P.findall(m_cuerpo.group(1)) if p.strip()]
    contenido_actual = "\n\n".join(parrafos_actuales)

    return {
        "archivo": archivo,
        "titulo": titulo,
        "fuente": fuente_nombre,
        "enlace": enlace,
        "guid": enlace,
        "fecha_publicacion_iso": fecha_iso,
        "categoria": categoria,
        "imagen_local": imagen_local,
        # Respaldo real (Plan B, ya publicado) por si Gemini falla para este ítem.
        "extracto_original": contenido_actual,
        "contenido_ampliado": contenido_actual,
    }


def reprocesar_con_gemini(item: dict) -> bool:
    """Intenta el mismo mecanismo real de resumir_ia.py (descargar la página
    original + Gemini). Devuelve True si logró un resumen nuevo; en ese
    caso deja item['resumen_ia']/['resumen_ia_ok'] listos para
    preparar_texto_mostrado()/preparar_resumen_ampliado(). Si falla, no
    toca esos campos -- quien llama debe dejar el ítem con su contenido de
    Plan B actual, sin inventar nada."""
    idioma = item.get("idioma", "en")
    html_pagina = ri.descargar_pagina(item["enlace"])
    if not html_pagina:
        return False
    texto_completo = ri.extraer_texto_de_pagina(html_pagina)
    if not texto_completo:
        return False
    resumen = ri.resumir_con_ia(texto_completo, item["titulo"])
    if not resumen:
        return False
    item["resumen_ia"] = resumen
    item["resumen_ia_ok"] = True
    return True


NOTA_GEMINI_MARCA = "Resumen generado con IA (Gemini)"


def procesar_fecha(fecha: str, dry_run: bool, solo_fallidos: bool, estado: dict) -> None:
    archivos = sorted(NOTICIA_DIR.glob(f"{fecha}-*.html"))
    log(f"=== {fecha}: {len(archivos)} página(s) ===")

    fuentes = fn.cargar_feeds()
    nombre_a_idioma = {f["nombre"]: f.get("idioma", "en") for f in fuentes}

    items: list[dict] = []
    exitosos = 0
    fallidos = 0
    for archivo in archivos:
        item = leer_item_publicado(archivo)
        if item is None:
            continue
        item["idioma"] = nombre_a_idioma.get(item["fuente"], "en")

        # --solo-fallidos: a los que YA tienen el resumen de Gemini (de una
        # pasada anterior de este mismo script) no se les vuelve a llamar a
        # la IA -- se conserva tal cual su resumen_ia actual (reconstruido
        # de la propia página, que a esta altura YA es el texto de Gemini,
        # no el de Plan B) marcándolos resumen_ia_ok=True, para no
        # gastar otra llamada ni arriesgar un reemplazo innecesario.
        ya_tenia_ia = NOTA_GEMINI_MARCA in archivo.read_text(encoding="utf-8")
        if solo_fallidos and ya_tenia_ia:
            item["resumen_ia"] = item["contenido_ampliado"]
            item["resumen_ia_ok"] = True
            items.append(item)
            continue

        item["resumen_ia"] = None
        item["resumen_ia_ok"] = False

        log(f"Procesando: {item['titulo'][:70]}...")
        if not dry_run:
            ok = reprocesar_con_gemini(item)
        else:
            ok = None
        if ok:
            exitosos += 1
            log(f"  OK: resumen con IA generado ({len(item['resumen_ia'])} caracteres).")
        elif ok is False:
            fallidos += 1
            estado.setdefault("fallidos", []).append(archivo.name)
            log("  AVISO: no se pudo generar resumen con IA para este ítem; se deja su contenido actual (Plan B).")
        items.append(item)

    log(f"{fecha}: {exitosos} con IA nueva, {fallidos} sin cambio (Plan B se mantiene).")

    if dry_run:
        return

    # Ordenar más reciente primero, igual que en cualquier publicación normal.
    items.sort(key=lambda x: x["fecha_publicacion_iso"], reverse=True)

    rutas_noticia = {item["enlace"]: f"/noticia/{item['archivo'].name}" for item in items}

    _orig_categorizar = bs.categorizar

    def _categorizar_preservada(it):
        return it["categoria"]

    bs.categorizar = _categorizar_preservada
    try:
        # 1. Regenerar cada página de detalle (preparar_texto_mostrado /
        # preparar_resumen_ampliado corren de verdad -- si hay resumen de
        # IA, lo usan; si no, caen al Plan B reconstruido arriba).
        for item in items:
            pagina = bs.render_pagina_noticia(item, rutas_noticia[item["enlace"]])
            item["archivo"].write_text(pagina, encoding="utf-8")

        # 2. Regenerar el archivo completo del día (edición autocontenida).
        items_html = bs._renderizar_grid(items, rutas_noticia)
        fecha_dt = datetime.strptime(fecha, "%Y-%m-%d")
        subtitulo = f"Edición del {bs.fecha_legible(fecha_dt)}"
        descripcion_edicion = (
            f"Titulares de ciberseguridad del {bs.fecha_legible(fecha_dt)}, "
            "agregados de fuentes públicas verificadas (The Hacker News, BleepingComputer, Krebs on Security y más). "
            "Un proyecto de DERENZIN S.A.S."
        )
        imagen_og_edicion = items[0].get("imagen_local") or "/assets/logo-derenzin.png"
        pagina_dia = bs.render_pagina_archivo_dia(fecha, subtitulo, items_html, descripcion_edicion, imagen_og_edicion)
        (ARCHIVO_DIR / f"{fecha}.html").write_text(pagina_dia, encoding="utf-8")
        log(f"Escrito {ARCHIVO_DIR / f'{fecha}.html'} ({len(items)} noticia(s)).")
    finally:
        bs.categorizar = _orig_categorizar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No llama a Gemini ni escribe nada; solo lista qué procesaría.")
    parser.add_argument("--solo-fallidos", action="store_true", help="Segunda pasada: solo reintenta los ítems que fallaron la vez anterior (ver reprocesar_ia_septiembre_estado.json).")
    args = parser.parse_args()

    if not ri.GEMINI_API_KEY:
        log("ERROR: GEMINI_API_KEY no está configurada en el entorno. Abortando (no se puede reprocesar sin clave real).")
        sys.exit(1)

    estado_path = RAIZ / "reprocesar_ia_septiembre_estado.json"
    estado = {"fallidos": []}
    if args.solo_fallidos and estado_path.exists():
        estado = json.loads(estado_path.read_text(encoding="utf-8"))

    for fecha in FECHAS:
        procesar_fecha(fecha, args.dry_run, args.solo_fallidos, estado)

    if not args.dry_run:
        estado_path.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Estado (ítems fallidos, si los hay) guardado en {estado_path}.")


if __name__ == "__main__":
    main()
