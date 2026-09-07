#!/usr/bin/env python3
"""
texto.py
--------
Utilidades de texto COMPARTIDAS entre fetch_news.py y build_site.py: es el
ÚNICO lugar del repositorio donde se decide cómo recortar un bloque de texto
a un máximo de caracteres y cómo señalar que quedó incompleto.

Antes había varias versiones de este recorte por separado (una en
fetch_news.py al guardar el "contenido ampliado", y otra distinta en
build_site.py para el resumen corto y otra más para el resumen ampliado de
la página de detalle) y cada una remataba el final del texto a su manera.
Eso hacía que el mismo tipo de problema se viera arreglado en un lugar y
roto en otro. Ahora todos pasan por las mismas dos funciones de acá.

Dos problemas concretos que motivan esto:

1. Algunas fuentes RSS (p.ej. BleepingComputer/WordPress) ya insertan SU
   PROPIO marcador de "leer más" al final del extracto, típicamente
   ".. [...]" — si no se reconoce, el código de acá le agrega otra elipsis
   ENCIMA y queda un texto visiblemente roto como "...gratuita... [...]…".
   `rematar_final()` primero quita cualquier marcador de este tipo y recién
   después decide si hace falta agregar el propio.

2. Otras fuentes (p.ej. The Hacker News) cortan su propio extracto de RSS a
   media frase SIN ningún marcador — el corte por límite de caracteres de
   este código puede entonces caer justo después de un conector suelto
   ("...no reveló el número de víctimas ni…"). `rematar_final()` quita esos
   conectores colgantes antes de agregar la elipsis.

Regla de oro (igual que en fetch_news.py y build_site.py): nunca se inventa
texto. Estas funciones solo deciden DÓNDE cortar lo que ya existe.
"""

from __future__ import annotations

import re

# Marcadores de "leer más"/corte que la propia fuente RSS a veces ya trae al
# final del extracto: corchetes con puntos suspensivos adentro ("[...]"),
# con o sin puntos sueltos antes ("..[...]"), y con cualquier cantidad de
# espacios. Exige que haya un corchete O 2+ puntos seguidos (nunca un solo
# punto final real, que es puntuación de cierre válida y no debe tocarse) —
# de ahí el "señuelo" obligatorio (\.{2,}|…|\[...\]) en el medio del patrón.
RE_MARCADOR_ORIGEN = re.compile(r"[\s.…\[\]]*(?:\.{2,}|…|\[[.\s…]*\])[\s.…\[\]]*$")

# Conectores que, si quedan como última "palabra" tras un corte por límite de
# caracteres, dejan el texto visiblemente incompleto (p.ej. "...la víctima
# ni…"). Se quitan, retrocediendo tantas veces como haga falta, antes de
# agregar la elipsis final. Incluye español (el idioma en que ya se muestra
# casi todo el texto, traducido) e inglés (por si la traducción falló y se
# muestra el original tal cual).
CONECTORES_COLGANTES = {
    # español
    "ni", "o", "u", "y", "e", "de", "del", "la", "el", "los", "las", "en",
    "con", "para", "por", "al", "a", "un", "una", "su", "sus", "que", "como",
    "sin",
    # inglés
    "or", "and", "the", "of", "in", "on", "for", "to", "with", "an", "no",
}


# Puntuación de cierre real -- si un texto termina en alguno de estos
# caracteres, se considera una oración/frase completa y no se le toca nada.
OK_ENDINGS = ".!?…\"'”)»"


def _quitar_conectores_colgantes(texto: str) -> str:
    partes = texto.split(" ")
    while len(partes) > 1 and partes[-1].lower().strip(",;:") in CONECTORES_COLGANTES:
        partes.pop()
    return " ".join(partes)


def fuente_parece_incompleta(texto: str) -> bool:
    """True si `texto` (tal cual lo entrega la fuente RSS, ANTES de que
    nosotros lo cortemos por longitud) ya llega incompleto: o bien trae su
    propio marcador de "leer más" (ver RE_MARCADOR_ORIGEN), o bien no
    termina en puntuación de cierre real. Se usa para decidir si hay que
    agregar una nota explícita de "resumen parcial" en vez de confiar en
    que la elipsis de rematar_final() alcance para avisarlo."""
    texto = texto.rstrip()
    if not texto:
        return False
    limpio = RE_MARCADOR_ORIGEN.sub("", texto).rstrip()
    if limpio != texto:
        return True  # tenía un marcador de origen tipo "[...]"
    return limpio[-1] not in OK_ENDINGS


def primeras_oraciones(texto: str, cantidad: int) -> str:
    """Devuelve las primeras `cantidad` oraciones completas de `texto`
    (corta en el punto/¡!/¿? de cierre de cada una) -- nunca a media
    palabra ni a media oración. Si `texto` tiene menos oraciones que
    `cantidad`, lo devuelve entero (rematado si hace falta)."""
    texto = texto.strip()
    posiciones = [m.end() for m in re.finditer(r"[.!?](?=\s|$)", texto)]
    if len(posiciones) >= cantidad:
        return texto[: posiciones[cantidad - 1]].strip()
    return rematar_final(texto)


def rematar_final(texto: str) -> str:
    """Punto ÚNICO donde se decide si un texto (ya recortado, por la razón
    que sea: límite de caracteres, límite de párrafos, o tal cual vino del
    RSS) queda marcado como incompleto. Se usa para el resumen corto, la
    meta descripción, el resumen ampliado y el contenido ampliado crudo —
    antes cada uno tenía su propio remate por separado.

    1. Quita cualquier marcador de "leer más" que la fuente RSS ya haya
       insertado (ver RE_MARCADOR_ORIGEN) — nunca se apila una elipsis
       propia ENCIMA de una que ya estaba ahí.
    2. Si lo que queda no termina en puntuación de cierre real, quita
       conectores sueltos que hayan quedado colgando al final y agrega "…".

    Nunca inventa palabras: solo decide si el final ya está señalado o hace
    falta señalarlo, y limpia lo que sobra para que se vea bien.
    """
    texto = texto.rstrip()
    texto = RE_MARCADOR_ORIGEN.sub("", texto).rstrip()
    if not texto:
        return texto
    if texto[-1] in OK_ENDINGS:
        return texto
    texto = _quitar_conectores_colgantes(texto)
    texto = texto.rstrip(",;: ")
    return f"{texto}…" if texto else texto


def truncar(texto: str, maximo: int) -> str:
    """Recorta `texto` a `maximo` caracteres sin cortar una oración a la
    mitad cuando se puede evitar: busca el último punto/¡!/¿? de cierre de
    oración dentro del límite y corta ahí; si no hay ninguno lo bastante
    adentro (una sola oración más larga que el límite), corta en el último
    espacio antes del tope. En los tres caminos de salida (texto que ya
    entraba entero, corte por oración, corte por palabra) pasa por
    `rematar_final()` — así cualquiera de los tres queda limpio de la misma
    forma, en vez de que cada camino remate el final a su manera."""
    texto = texto.strip()
    if len(texto) <= maximo:
        return rematar_final(texto)

    fragmento = texto[:maximo]
    ultimo_punto = -1
    for m in re.finditer(r"[.!?](?=\s|$)", fragmento):
        ultimo_punto = m.end()
    # Solo usar el corte por oración si no deja el resumen demasiado corto
    # (p.ej. si la primera oración ya ocupa casi todo el límite, preferimos
    # eso a un corte por palabra que deje un fragmento minúsculo).
    if ultimo_punto >= maximo * 0.4:
        return rematar_final(fragmento[:ultimo_punto])

    cortado = fragmento.rsplit(" ", 1)[0]
    return rematar_final(cortado)


def acotar_parrafos(parrafos: list[str], maximo_caracteres: int, maximo_parrafos: int) -> list[str]:
    """Se queda con párrafos ENTEROS (nunca corta uno a la mitad) hasta
    llegar al tope de caracteres o de cantidad de párrafos. Siempre devuelve
    al menos un párrafo (aunque ese solo ya supere el tope)."""
    resultado: list[str] = []
    total = 0
    for p in parrafos:
        if resultado and (total + len(p) > maximo_caracteres or len(resultado) >= maximo_parrafos):
            break
        resultado.append(p)
        total += len(p)
    return resultado or parrafos[:1]
