#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reprocesar_ia_general.py
-------------------------
Script de reprocesamiento PUNTUAL (no forma parte del workflow diario):
versión general de reprocesar_ia_septiembre.py -- en vez de limitarse a los
4 días del backfill de septiembre, recorre TODAS las páginas de detalle ya
publicadas en site/noticia/ y reintenta el resumen con IA (Gemini) para las
que cayeron en el "Plan B" (extracto corto de RSS, sin el resumen ampliado
de 3-4 párrafos) -- típicamente porque resumir_ia.py falló ese día (cuota de
Gemini agotada, timeout, artículo no descargable, etc.), no porque la
fuente sea así de corta.

Motivo de este script (2026-09-17): al contar párrafos publicados por día
se detectó que el resumen con IA viene fallando cada vez más seguido (6% de
las noticias del 14/09 sin resumen de IA -> 54% el 17/09) -- ver el cambio
de modelo a gemini-3.5-flash en resumir_ia.py, que debería reducir esto
para las noticias NUEVAS de acá en adelante. Este script es el backfill
para las que YA quedaron publicadas en Plan B mientras tanto.

Cómo detecta un candidato (nunca por una marca que ya no existe en la
página -- el aviso de transparencia se sacó de la plantilla, ver
quitar_aviso_transparencia_ia.py):
  - Cuenta los <p> dentro de noticia-detalle-cuerpo. El resumen con IA
    real siempre viene en 3-4 párrafos (ver el prompt en resumir_ia.py);
    el Plan B casi siempre es 1-2. Se usa <= UMBRAL_PARRAFOS (default: 2)
    como corte -- conservador a propósito, para no reprocesar páginas que
    ya tienen un resumen real solo porque son un poco cortas.
  - Excluye las páginas de "análisis original" (llevan el aviso
    AVISO_ANALISIS_ORIGINAL, que nunca se sacó de la plantilla): no son un
    resumen de RSS, son piezas propias de DERENZIN S.A.S., no les
    corresponde este reprocesamiento.
  - Excluye las de Protección de Datos / SPDP (enlace bajo
    SPDP_PRENSA_URL): fetch_spdp.py ya las marca omitir_resumen_ia porque
    su URL de fuente es compartida entre varios boletines -- entrar ahí
    traería una mezcla de boletines, no el texto de este en particular.

Mismo mecanismo real que resumir_ia.py para las noticias del día a día:
entra a la URL original de cada candidato (ya verificada, leída de la
propia página publicada), extrae el texto completo con trafilatura, y le
pide a Gemini (GEMINI_MODEL de resumir_ia.py) un resumen fiel en español.
Nunca inventa nada -- si Gemini falla para algún ítem, esa página se deja
tal cual estaba (con su contenido real de Plan B), nunca se reemplaza por
texto inventado.

El título se reconstruye leyéndolo de la propia página ya publicada (ya
está mostrado en español si correspondía) -- se fuerza idioma="es" al
reconstruir el ítem para que preparar_texto_mostrado()/
preparar_resumen_ampliado() nunca intenten volver a traducirlo con DeepL
(evita una doble traducción sobre un título que ya está traducido).

Cuota de Gemini: procesa como mucho --limite ítems por corrida (default:
40, margen conservador bajo el límite diario del free tier) y lleva un
ledger en data/reprocesar_ia_general_estado.json para no reprocesar en la
próxima corrida los que ya quedaron con resumen de IA, ni gastar otra
llamada en los que ya fallaron (salvo --reintentar-fallidos). Hay que
volver a correr este script (una vez por corrida de --limite) hasta que el
log diga "0 candidato(s) pendiente(s)".

Alcance de lo que actualiza: la página de detalle de cada ítem reprocesado
con éxito, y site/archivo/AAAA-MM-DD.html de cada día que tuvo al menos un
cambio (se reconstruye completo a partir de TODOS los ítems publicados ese
día, preservando los que no cambiaron). También refresca site/categoria/*
al final (generar_paginas_categoria(), mismo mecanismo que
--regenerar-categorias) para que las tarjetas de categoría reflejen el
resumen nuevo. A propósito NO toca site/index.html: la portada siempre
refleja las noticias más recientes tal como las generó el workflow diario
real, y ese contenido se autocorrige solo con la próxima corrida diaria
normal a medida que las noticias de hoy van rotando fuera de portada.

Uso:
    GEMINI_API_KEY=<clave> py -3 scripts/reprocesar_ia_general.py --dry-run
    GEMINI_API_KEY=<clave> py -3 scripts/reprocesar_ia_general.py
    GEMINI_API_KEY=<clave> py -3 scripts/reprocesar_ia_general.py --limite 60
    GEMINI_API_KEY=<clave> py -3 scripts/reprocesar_ia_general.py --reintentar-fallidos
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_site as bs  # noqa: E402
import fetch_news as fn  # noqa: E402
import resumir_ia as ri  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
NOTICIA_DIR = RAIZ / "site" / "noticia"
ARCHIVO_DIR = RAIZ / "site" / "archivo"
ESTADO_PATH = RAIZ / "data" / "reprocesar_ia_general_estado.json"

UMBRAL_PARRAFOS = 2
LIMITE_POR_DEFECTO = 40

SPDP_PRENSA_URL = "https://spdp.gob.ec/prensa/"

RE_TITULO = re.compile(r'<h1 class="noticia-detalle-titulo">(.*?)</h1>', re.DOTALL)
RE_EYEBROW = re.compile(r'<span class="eyebrow-categoria">([^<]*)</span>')
RE_FUENTE_ENLACE = re.compile(r'<span class="fuente">Fuente: <a href="([^"]+)"[^>]*>([^<]*)</a></span>')
RE_FECHA_JSONLD = re.compile(r'"datePublished":\s*"([^"]+)"')
RE_IMG_REAL = re.compile(
    r'noticia-imagen--destacada">\s*<span class="categoria-badge">[^<]*</span>\s*<img src="(/imagenes/[^"]+)"'
)
RE_CUERPO = re.compile(r'noticia-detalle-cuerpo">\n(.*?)\n\s*</div>', re.DOTALL)
RE_P = re.compile(r"<p>(.*?)</p>", re.DOTALL)

ETIQUETA_A_SLUG = {info["etiqueta"]: slug for slug, info in bs.CATEGORIAS.items()}
ETIQUETA_A_SLUG[bs.GENERICO["etiqueta"]] = "generico"


def log(mensaje: str) -> None:
    print(f"[reprocesar_ia_general] {mensaje}", flush=True)


def _texto_plano(html_fragmento: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]*>", "", html_fragmento)).strip()


def leer_item_publicado(archivo: Path) -> dict | None:
    """Reconstruye los campos de un ítem a partir de su página de detalle YA
    publicada -- no depende de data/nuevas_hoy.json (que a esta altura del
    histórico ya se sobreescribió muchas veces)."""
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
        # El título leído de la página ya está mostrado en español (o en el
        # idioma que corresponda si no se pudo traducir en su momento) --
        # forzar idioma="es" evita que preparar_texto_mostrado() intente
        # traducirlo de nuevo con DeepL como si fuera el título original.
        "idioma": "es",
        "fuente": fuente_nombre,
        "enlace": enlace,
        "guid": enlace,
        "fecha_publicacion_iso": fecha_iso,
        "categoria": categoria,
        "imagen_local": imagen_local,
        "es_spdp": enlace.startswith(SPDP_PRENSA_URL),
        "n_parrafos_actuales": len(parrafos_actuales),
        # Respaldo real (Plan B, ya publicado) por si Gemini falla para este ítem.
        "extracto_original": contenido_actual,
        "contenido_ampliado": contenido_actual,
    }


def es_analisis_original(archivo: Path) -> bool:
    return bs.AVISO_ANALISIS_ORIGINAL in archivo.read_text(encoding="utf-8")


def reprocesar_con_gemini(item: dict) -> bool:
    """Mismo mecanismo real de resumir_ia.py (descargar la página original +
    Gemini). Devuelve True si logró un resumen nuevo -- en ese caso deja
    item['resumen_ia']/['resumen_ia_ok'] listos para
    preparar_texto_mostrado()/preparar_resumen_ampliado(). Si falla, no
    toca esos campos -- quien llama debe dejar el ítem con su contenido de
    Plan B actual, sin inventar nada."""
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


def cargar_estado() -> dict:
    if ESTADO_PATH.exists():
        return json.loads(ESTADO_PATH.read_text(encoding="utf-8"))
    return {"ok": [], "fallidos": []}


def guardar_estado(estado: dict) -> None:
    ESTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    ESTADO_PATH.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


def fecha_de_archivo(nombre: str) -> str | None:
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-", nombre)
    return m.group(1) if m else None


def regenerar_dia(fecha: str, resumenes_nuevos: dict[str, str], sidebar_html_general: str) -> None:
    """Reconstruye site/archivo/AAAA-MM-DD.html y CADA página de detalle
    del día, a partir de TODAS las páginas de detalle ya publicadas para
    esa fecha (reprocesadas en esta corrida o no) -- mismo patrón que
    reprocesar_ia_septiembre.py: cada día es una edición autocontenida que
    se puede reconstruir entera desde lo ya publicado.

    `resumenes_nuevos` mapea nombre de archivo -> texto del resumen que
    Gemini acaba de generar en ESTA corrida (si lo hay) -- se usa en vez
    de releer la página vieja de disco, que todavía tiene el Plan B."""
    archivos = sorted(NOTICIA_DIR.glob(f"{fecha}-*.html"))
    if not archivos:
        return

    fuentes = fn.cargar_feeds()
    nombre_a_idioma = {f["nombre"]: f.get("idioma", "en") for f in fuentes}

    items = []
    for archivo in archivos:
        item = leer_item_publicado(archivo)
        if item is None:
            continue
        resumen_nuevo = resumenes_nuevos.get(archivo.name)
        if resumen_nuevo:
            # Reprocesado recién en esta corrida: usar el resumen de
            # Gemini que ya se obtuvo, sin volver a llamar a la API.
            item["resumen_ia"] = resumen_nuevo
            item["resumen_ia_ok"] = True
        else:
            # Ítem sin cambios en esta corrida: si YA tenía un resumen de
            # IA real (de una publicación normal o de una corrida
            # anterior de este mismo script), se preserva como tal para
            # que render_pagina_noticia() no lo degrade a Plan B por
            # error; si no, se deja en Plan B con su contenido actual.
            marca_ia = bs.NOTA_RESUMEN_IA in archivo.read_text(encoding="utf-8") or item["n_parrafos_actuales"] >= 3
            if marca_ia:
                item["resumen_ia"] = item["contenido_ampliado"]
                item["resumen_ia_ok"] = True
            else:
                item["resumen_ia"] = None
                item["resumen_ia_ok"] = False
        item["idioma"] = "es"  # el título/contenido leído ya está mostrado en español
        items.append(item)

    if not items:
        return

    items.sort(key=lambda x: x["fecha_publicacion_iso"], reverse=True)
    rutas_noticia = {item["enlace"]: f"/noticia/{item['archivo'].name}" for item in items}

    _orig_categorizar = bs.categorizar

    def _categorizar_preservada(it):
        return it["categoria"]

    bs.categorizar = _categorizar_preservada
    try:
        # 1. Regenerar cada página de detalle del día (preparar_texto_mostrado/
        #    preparar_resumen_ampliado corren de verdad -- los reprocesados
        #    usan el resumen nuevo, el resto conserva su contenido actual).
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
        pagina_dia = bs.render_pagina_archivo_dia(
            fecha, subtitulo, items_html, descripcion_edicion, imagen_og_edicion, sidebar_html_general
        )
        (ARCHIVO_DIR / f"{fecha}.html").write_text(pagina_dia, encoding="utf-8")
        log(f"  Regenerado {ARCHIVO_DIR / f'{fecha}.html'} y {len(items)} página(s) de detalle.")
    finally:
        bs.categorizar = _orig_categorizar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="No llama a Gemini ni escribe nada; solo lista candidatos.")
    parser.add_argument("--limite", type=int, default=LIMITE_POR_DEFECTO, help=f"Máximo de ítems a reprocesar en esta corrida (default: {LIMITE_POR_DEFECTO}).")
    parser.add_argument("--umbral-parrafos", type=int, default=UMBRAL_PARRAFOS, help=f"Un ítem con este número de párrafos o menos se considera candidato (default: {UMBRAL_PARRAFOS}).")
    parser.add_argument("--reintentar-fallidos", action="store_true", help="Vuelve a intentar los ítems que fallaron en corridas anteriores (por defecto se saltan).")
    args = parser.parse_args()

    if not ri.GEMINI_API_KEY and not args.dry_run:
        log("ERROR: GEMINI_API_KEY no está configurada en el entorno. Abortando (no se puede reprocesar sin clave real).")
        sys.exit(1)

    estado = cargar_estado()
    ya_ok = set(estado.get("ok", []))
    ya_fallidos = set(estado.get("fallidos", []))

    archivos = sorted(NOTICIA_DIR.glob("*.html"))
    log(f"{len(archivos)} página(s) de noticia encontradas en {NOTICIA_DIR}.")

    candidatos: list[Path] = []
    excluidos_analisis = 0
    excluidos_spdp = 0
    for archivo in archivos:
        if archivo.name in ya_ok:
            continue
        if archivo.name in ya_fallidos and not args.reintentar_fallidos:
            continue
        if es_analisis_original(archivo):
            excluidos_analisis += 1
            continue
        item = leer_item_publicado(archivo)
        if item is None:
            continue
        if item["es_spdp"]:
            excluidos_spdp += 1
            continue
        if item["n_parrafos_actuales"] <= args.umbral_parrafos:
            candidatos.append(archivo)

    log(
        f"Candidatos totales: {len(candidatos)} (excluidos: {excluidos_analisis} análisis original, "
        f"{excluidos_spdp} Protección de Datos/SPDP, {len(ya_ok)} ya con IA de una corrida anterior, "
        f"{len(ya_fallidos) if not args.reintentar_fallidos else 0} ya fallidos)."
    )

    if args.dry_run:
        for archivo in candidatos[: args.limite]:
            log(f"  [dry-run] procesaría: {archivo.name}")
        log(f"(--dry-run: no se llamó a Gemini ni se escribió nada. {len(candidatos)} candidato(s) pendiente(s) en total.)")
        return

    lote = candidatos[: args.limite]
    log(f"Procesando {len(lote)} ítem(s) en esta corrida (límite: {args.limite}).")

    fechas_afectadas: set[str] = set()
    resumenes_nuevos: dict[str, str] = {}
    exitosos = 0
    fallidos = 0
    for archivo in lote:
        item = leer_item_publicado(archivo)
        if item is None:
            continue
        log(f"Procesando: {item['titulo'][:70]}...")
        item["resumen_ia"] = None
        item["resumen_ia_ok"] = False
        ok = reprocesar_con_gemini(item)
        if ok:
            exitosos += 1
            ya_ok.add(archivo.name)
            ya_fallidos.discard(archivo.name)
            resumenes_nuevos[archivo.name] = item["resumen_ia"]
            fecha = fecha_de_archivo(archivo.name)
            if fecha:
                fechas_afectadas.add(fecha)
            log(f"  OK: resumen con IA generado ({len(item['resumen_ia'])} caracteres).")
        else:
            fallidos += 1
            ya_fallidos.add(archivo.name)
            log("  AVISO: no se pudo generar resumen con IA para este ítem; se deja su contenido actual (Plan B).")

    log(f"Listo: {exitosos} con IA nueva, {fallidos} sin cambio (Plan B se mantiene) de {len(lote)} intentados.")

    estado["ok"] = sorted(ya_ok)
    estado["fallidos"] = sorted(ya_fallidos)
    guardar_estado(estado)

    if fechas_afectadas:
        log(f"Regenerando página(s) de detalle y archivo del día para {len(fechas_afectadas)} fecha(s) afectada(s)...")
        sidebar_html_general = bs.render_sidebar_noticia(bs.obtener_items_recientes(None))
        for fecha in sorted(fechas_afectadas):
            regenerar_dia(fecha, resumenes_nuevos, sidebar_html_general)

        log("Refrescando páginas de categoría (generar_paginas_categoria)...")
        bs.generar_paginas_categoria(sidebar_html_general)

    log(f"{len(candidatos) - len(lote)} candidato(s) pendiente(s) para una próxima corrida.")


if __name__ == "__main__":
    main()
