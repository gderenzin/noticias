#!/usr/bin/env python3
"""
build_site.py
--------------
Toma data/nuevas_hoy.json (generado por fetch_news.py) y:

  1. Redacta un "resumen" corto por ítem, en español, basado ESTRICTAMENTE en
     el título y el extracto que trae el propio RSS (nunca se inventa nada).
  2. Genera/actualiza site/index.html (portada de hoy).
  3. Crea site/archivo/AAAA-MM-DD.html con la edición del día.
  4. Regenera site/archivo/index.html (listado de ediciones anteriores).
  5. Actualiza data/publicadas.json (el ledger) con los enlaces publicados.

Nota sobre el "resumen en español": este script NO usa ningún servicio de
traducción automática ni ninguna API externa (por diseño: la primera versión
debe funcionar sin secretos configurados, y traducir con un modelo introduce
riesgo de alterar el significado del RSS original). Para fuentes en español
se usa el extracto del RSS tal cual. Para fuentes en inglés se arma un texto
en español que ENVUELVE el título/extracto original entre comillas, sin
traducirlo, dejando explícito que el contenido original está en otro idioma.
Así se cumple la regla de oro de no inventar ni alterar contenido.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NUEVAS_JSON = RAIZ / "data" / "nuevas_hoy.json"
PUBLICADAS_JSON = RAIZ / "data" / "publicadas.json"
SITE_DIR = RAIZ / "site"
ARCHIVO_DIR = SITE_DIR / "archivo"

# Guayaquil = UTC-5 todo el año (Ecuador no usa horario de verano)
ZONA_GUAYAQUIL = timezone(timedelta(hours=-5))

AVISO_LEGAL = (
    "Este sitio agrega titulares y resúmenes de fuentes públicas verificadas; "
    "el contenido pertenece a sus autores originales. Consulta el enlace de "
    "cada fuente para leer el artículo completo."
)

MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def log(mensaje: str) -> None:
    print(f"[build_site] {mensaje}", flush=True)


def cargar_nuevas() -> list[dict]:
    if not NUEVAS_JSON.exists():
        log(f"AVISO: no existe {NUEVAS_JSON}; nada que hacer (¿corriste fetch_news.py antes?).")
        return []
    with open(NUEVAS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def cargar_publicadas() -> dict:
    if not PUBLICADAS_JSON.exists():
        return {"urls": {}}
    try:
        with open(PUBLICADAS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"urls": {}}


def guardar_publicadas(datos: dict) -> None:
    PUBLICADAS_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(PUBLICADAS_JSON, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)


def truncar(texto: str, maximo: int) -> str:
    if len(texto) <= maximo:
        return texto
    cortado = texto[:maximo].rsplit(" ", 1)[0]
    return cortado + "…"


def generar_resumen(item: dict) -> str:
    """Genera el resumen en español SIN traducir ni inventar contenido.

    Se apoya únicamente en item['extracto_original'] e item['titulo'], tal
    como llegaron del RSS (ver fetch_news.py). Nunca agrega cifras, nombres
    ni hechos que no estén en ese texto.
    """
    titulo = item["titulo"]
    idioma = item.get("idioma", "en")
    extracto = (item.get("extracto_original") or "").strip()

    # El extracto de RSS a veces solo repite el título; en ese caso no aporta nada.
    aporta_info = bool(extracto) and extracto.lower() != titulo.strip().lower() and len(extracto) > 15

    if idioma == "es":
        if aporta_info:
            return truncar(extracto, 600)
        return f'El RSS de la fuente no trae un extracto adicional; solo se dispone del titular: "{titulo}".'

    # Fuente en idioma distinto al español: no se traduce, se cita el original.
    partes = [f'Titular original: "{titulo}".']
    if aporta_info:
        partes.append(f'Extracto original del RSS: "{truncar(extracto, 500)}"')
    else:
        partes.append("El RSS de la fuente no trae un extracto adicional aparte del titular.")
    partes.append(
        f"(Contenido original en {'inglés' if idioma == 'en' else idioma}; "
        "no se tradujo automáticamente para evitar alterar el significado — "
        "consulta el enlace de la fuente para leer el artículo completo.)"
    )
    return " ".join(partes)


def fecha_legible(dt: datetime) -> str:
    return f"{dt.day} de {MESES_ES[dt.month - 1]} de {dt.year}"


def render_item_html(item: dict, resumen: str) -> str:
    titulo = escape(item["titulo"])
    fuente = escape(item["fuente"])
    enlace = escape(item["enlace"], quote=True)
    fecha_original = escape(item.get("fecha_publicacion_original") or item["fecha_publicacion_iso"])
    resumen_html = escape(resumen).replace("\n", "<br>")
    idioma = item.get("idioma", "en")
    etiqueta_idioma = "ES" if idioma == "es" else idioma.upper()

    return f"""    <article class="noticia">
      <div class="noticia-meta">
        <span class="fuente">{fuente}</span>
        <span class="idioma" title="Idioma del contenido original">{etiqueta_idioma}</span>
        <span class="fecha">{fecha_original}</span>
      </div>
      <h2 class="noticia-titulo"><a href="{enlace}" rel="noopener noreferrer" target="_blank">{titulo}</a></h2>
      <p class="noticia-resumen">{resumen_html}</p>
      <a class="noticia-enlace" href="{enlace}" rel="noopener noreferrer" target="_blank">Leer artículo original en {fuente} →</a>
    </article>
"""


def render_pagina_index(titulo_pagina: str, subtitulo: str, items_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(titulo_pagina)}</title>
  <meta name="description" content="Periódico digital de ciberseguridad: titulares diarios con enlace directo a la fuente original.">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="cabecera">
    <div class="cabecera-contenido">
      <h1><a href="index.html">🛡️ Periódico de Ciberseguridad</a></h1>
      <p class="subtitulo">{escape(subtitulo)}</p>
      <nav class="nav">
        <a href="index.html">Portada</a>
        <a href="archivo/index.html">Archivo</a>
      </nav>
    </div>
  </header>

  <main class="contenido">
{items_html}
  </main>

  <footer class="pie">
    <p>{escape(AVISO_LEGAL)}</p>
    <p class="pie-fuentes">Fuentes monitoreadas: The Hacker News, BleepingComputer, Krebs on Security, Dark Reading, WeLiveSecurity (ESET), INCIBE-CERT.</p>
  </footer>
</body>
</html>
"""


def render_pagina_archivo_dia(fecha_str: str, subtitulo: str, items_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Ciberseguridad — edición del {escape(fecha_str)}</title>
  <meta name="description" content="Periódico digital de ciberseguridad: titulares diarios con enlace directo a la fuente original.">
  <link rel="stylesheet" href="../style.css">
</head>
<body>
  <header class="cabecera">
    <div class="cabecera-contenido">
      <h1><a href="../index.html">🛡️ Periódico de Ciberseguridad</a></h1>
      <p class="subtitulo">{escape(subtitulo)}</p>
      <nav class="nav">
        <a href="../index.html">Portada</a>
        <a href="index.html">Archivo</a>
      </nav>
    </div>
  </header>

  <main class="contenido">
{items_html}
  </main>

  <footer class="pie">
    <p>{escape(AVISO_LEGAL)}</p>
    <p class="pie-fuentes">Fuentes monitoreadas: The Hacker News, BleepingComputer, Krebs on Security, Dark Reading, WeLiveSecurity (ESET), INCIBE-CERT.</p>
  </footer>
</body>
</html>
"""


def render_archivo_index(dias: list[str]) -> str:
    if dias:
        filas = "\n".join(
            f'      <li><a href="{d}.html">{fecha_legible(datetime.strptime(d, "%Y-%m-%d"))}</a></li>'
            for d in sorted(dias, reverse=True)
        )
        lista = f"<ul class=\"lista-archivo\">\n{filas}\n    </ul>"
    else:
        lista = "<p>Todavía no hay ediciones archivadas.</p>"

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Archivo — Periódico de Ciberseguridad</title>
  <link rel="stylesheet" href="../style.css">
</head>
<body>
  <header class="cabecera">
    <div class="cabecera-contenido">
      <h1><a href="../index.html">🛡️ Periódico de Ciberseguridad</a></h1>
      <p class="subtitulo">Archivo de ediciones anteriores</p>
      <nav class="nav">
        <a href="../index.html">Portada</a>
        <a href="index.html">Archivo</a>
      </nav>
    </div>
  </header>

  <main class="contenido">
    {lista}
  </main>

  <footer class="pie">
    <p>{escape(AVISO_LEGAL)}</p>
  </footer>
</body>
</html>
"""


def main() -> None:
    nuevos = cargar_nuevas()

    if not nuevos:
        log("Sin novedades hoy: no se genera ninguna página nueva ni se toca el ledger.")
        return

    ahora_gye = datetime.now(ZONA_GUAYAQUIL)
    fecha_str = ahora_gye.strftime("%Y-%m-%d")
    subtitulo = f"Edición del {fecha_legible(ahora_gye)} — {len(nuevos)} noticia(s) nueva(s)"

    items_html = ""
    for item in nuevos:
        resumen = generar_resumen(item)
        items_html += render_item_html(item, resumen)

    # 1. Portada (index.html)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    index_html = render_pagina_index("Periódico de Ciberseguridad — Portada", subtitulo, items_html)
    with open(SITE_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(index_html)
    log(f"Escrito {SITE_DIR / 'index.html'}")

    # 2. Página de archivo del día
    ARCHIVO_DIR.mkdir(parents=True, exist_ok=True)
    pagina_dia = render_pagina_archivo_dia(fecha_str, subtitulo, items_html)
    ruta_dia = ARCHIVO_DIR / f"{fecha_str}.html"
    if ruta_dia.exists():
        # Ya hubo una edición hoy (p.ej. se corrió manualmente dos veces): se
        # anexan las noticias nuevas a la página de archivo existente en vez
        # de sobreescribir lo ya publicado.
        anterior = ruta_dia.read_text(encoding="utf-8")
        marcador = "  <main class=\"contenido\">\n"
        if marcador in anterior:
            anterior = anterior.replace(marcador, marcador + items_html, 1)
            ruta_dia.write_text(anterior, encoding="utf-8")
            log(f"Actualizado {ruta_dia} (ya existía una edición de hoy; se anexaron los ítems nuevos).")
        else:
            ruta_dia.write_text(pagina_dia, encoding="utf-8")
            log(f"Reescrito {ruta_dia} (no se pudo anexar de forma segura).")
    else:
        ruta_dia.write_text(pagina_dia, encoding="utf-8")
        log(f"Escrito {ruta_dia}")

    # 3. Índice de archivo
    dias_existentes = sorted({p.stem for p in ARCHIVO_DIR.glob("*.html") if p.stem != "index"})
    with open(ARCHIVO_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(render_archivo_index(dias_existentes))
    log(f"Escrito {ARCHIVO_DIR / 'index.html'} ({len(dias_existentes)} edición/ediciones listadas)")

    # 4. Ledger de publicadas
    publicadas = cargar_publicadas()
    urls = publicadas.setdefault("urls", {})
    for item in nuevos:
        urls[item["enlace"]] = {
            "guid": item["guid"],
            "fuente": item["fuente"],
            "titulo": item["titulo"],
            "fecha_publicacion_iso": item["fecha_publicacion_iso"],
            "fecha_agregada_iso": datetime.now(timezone.utc).isoformat(),
        }
    guardar_publicadas(publicadas)
    log(f"Ledger actualizado: {PUBLICADAS_JSON} ahora tiene {len(urls)} URL(s) registradas.")

    log(f"Listo: {len(nuevos)} noticia(s) publicada(s) en la edición del {fecha_str}.")


if __name__ == "__main__":
    main()
