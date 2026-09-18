# -*- coding: utf-8 -*-
"""
hifenar.py
----------
Inserta guiones blandos (U+00AD, "&shy;") en las palabras largas en español del
cuerpo de los artículos, para que el texto justificado pueda cortar palabras
al final de línea en CUALQUIER navegador.

Por qué existe: `hyphens: auto` depende de que el navegador tenga instalado un
diccionario de guiones para el idioma. Se comprobó (Chromium 152 de Electron)
que soporta la propiedad pero NO hifena ni en español ni en inglés, así que el
justificado abría huecos de hasta 128px en móvil. Los guiones blandos, en
cambio, los respetan todos los navegadores sin diccionario (solo aparecen como
guion visible cuando la palabra realmente se corta).

Usa pyphen (diccionario `es`). Si pyphen no está instalado, no hace nada (el
sitio sigue funcionando, solo sin cortes de palabra): nunca rompe el build.
"""

from __future__ import annotations

import re

try:  # pragma: no cover - depende del entorno
    import pyphen

    _DIC = pyphen.Pyphen(lang="es", left=2, right=2)
except Exception:  # pyphen ausente o sin diccionario
    _DIC = None

GUION_BLANDO = "­"

# Solo palabras 100% alfabéticas de 6+ letras: sin dígitos, sin URLs, sin
# nombres con símbolos. Los entities HTML (&amp; &quot; &#x27; ...) tienen
# nombres de <=5 letras seguidas, así que con este mínimo nunca se tocan.
_RE_PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]{6,}")


def _hifenar_palabra(m: re.Match) -> str:
    palabra = m.group(0)
    # Siglas en mayúsculas (ESET, INCIBE...) nunca se cortan.
    if palabra.isupper():
        return palabra
    return _DIC.inserted(palabra, hyphen=GUION_BLANDO)


def hifenar_texto(texto: str) -> str:
    """Devuelve `texto` con guiones blandos en sus palabras largas. Idempotente."""
    if _DIC is None or not texto or GUION_BLANDO in texto:
        return texto
    return _RE_PALABRA.sub(_hifenar_palabra, texto)
