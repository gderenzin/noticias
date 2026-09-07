# Periódico Digital de Ciberseguridad

Sitio estático que se actualiza **solo, una vez al día**, con titulares reales de
ciberseguridad tomados de una lista fija de feeds RSS verificados. Nunca se
publica una noticia sin que exista un ítem real de RSS que la respalde, ni sin
un enlace directo a la fuente original.

Publicado en: **https://noticias.derenzin.com** (una vez completes los pasos de
DNS/Pages más abajo).

---

## Regla de oro (cómo se evita inventar noticias)

1. Las fuentes están fijas en [`feeds.yaml`](feeds.yaml). El código nunca busca
   en la web libre ni genera una noticia por su cuenta.
2. Cada fuente declara su `dominio` oficial. Antes de publicar cualquier ítem,
   `scripts/fetch_news.py` comprueba que el enlace del artículo pertenezca
   realmente a ese dominio (o a un subdominio de este). Si no coincide, se
   descarta.
3. El "resumen" de cada noticia se arma **únicamente** con el título y el
   extracto que trae el propio RSS — nunca se agregan cifras, nombres ni
   hechos que no estén en ese texto.
4. **Sobre la traducción**: todo el sitio se muestra en español (títulos,
   resúmenes y textos de interfaz), incluso cuando la fuente original está en
   inglés. La traducción se hace con la API de DeepL y **solo traduce el
   título y el extracto que ya vienen del RSS** — nunca agrega cifras,
   nombres ni hechos nuevos. Si la traducción falla por cualquier motivo (sin
   clave configurada, red caída, cuota agotada), esa noticia en particular
   **no se inventa una traducción**: se muestra el título/extracto original
   entre comillas con una nota aclaratoria en español. Ver la sección
   "Traducción al español (DeepL)" más abajo para configurarlo.
5. Cada noticia lleva siempre: título, fuente, fecha de publicación original y
   enlace directo al artículo. Al pie de cada página va el aviso legal fijo
   exigido por la especificación.
6. Si un feed individual falla ese día (timeout, 404, XML inválido), se salta
   y se sigue con los demás — no se rompe la ejecución completa por un feed
   caído. Si ningún feed trae novedades, no se publica nada y el workflow
   igual termina en verde (no es un error).
7. **Sobre las imágenes**: solo se usa una imagen si el propio ítem de RSS la
   trae de forma estructurada (`<enclosure>` o `<media:content>`). Nunca se
   genera ni se pide a una IA que invente una foto del hecho. Si el RSS no
   trae imagen, se muestra un ícono ilustrativo genérico por categoría,
   claramente etiquetado como tal. Ver la sección "Imágenes" más abajo.

---

## Estado de las fuentes (feeds.yaml)

Se validaron las 6 fuentes pedidas haciendo una petición HTTP real a cada una
el día en que se armó este repositorio. **Las 6 respondieron XML de RSS/Atom
válido** y quedaron activas en `feeds.yaml`:

| Fuente | Estado | Nota |
|---|---|---|
| The Hacker News | ✅ activa | El feed se sirve vía Feedburner (`feeds.feedburner.com`), pero los artículos enlazan a `thehackernews.com`. Por eso `dominio` en feeds.yaml apunta a `thehackernews.com`, no al host del feed. |
| BleepingComputer | ✅ activa | — |
| Krebs on Security | ✅ activa | Publica con poca frecuencia (a veces 0 ítems nuevos en la ventana de 48h); es normal, no es un fallo. |
| Dark Reading | ✅ activa | Su feed mezcla noticias con páginas de "eventos virtuales" que traen fechas fuera de la ventana normal; `fetch_news.py` las descarta automáticamente por el chequeo de fecha (ver más abajo). |
| WeLiveSecurity (ESET) en español | ✅ activa | — |
| INCIBE-CERT - Avisos | ✅ activa (marcada `opcional: true`) | Si en el futuro este feed deja de responder XML válido, el workflow simplemente lo salta (queda en el log) y no rompe la ejecución. Si quieres que se elimine formalmente de `feeds.yaml`, dímelo y lo hago cuando falle.|

**Ninguna fuente tuvo que descartarse** en esta versión inicial. Si con el
tiempo alguna deja de responder, el proceso la salta sola (ver "Lógica de
fetch_news.py" en la especificación); revisa los logs del workflow en GitHub
Actions para verlo.

### Cómo agregar o quitar una fuente

Edita `feeds.yaml` y agrega/quita un bloque como:

```yaml
  - nombre: "Nombre para mostrar"
    url: "https://.../feed"
    dominio: "dominio-de-los-articulos.com"
    idioma: en   # o es
    opcional: true   # opcional: si falla, no debe romper el workflow
```

`dominio` debe ser el dominio real donde viven los ARTÍCULOS (no necesariamente
el host del feed — ver el caso de The Hacker News arriba). Sin este campo
correcto, todos los ítems de esa fuente se descartarán por "dominio no
coincidente".

---

## Estructura del repositorio

```
feeds.yaml                       Lista blanca de fuentes RSS (única fuente de verdad)
scripts/fetch_news.py            Descarga feeds, filtra, deduplica -> data/nuevas_hoy.json
scripts/build_site.py            Genera el HTML a partir de data/nuevas_hoy.json
data/publicadas.json             Ledger de URLs ya publicadas (para no duplicar)
data/imagenes_descargadas.json   Ledger de imágenes ya descargadas (URL original -> ruta local)
data/nuevas_hoy.json             Archivo transitorio (no se versiona, ver .gitignore)
site/index.html                  Portada con la edición más reciente
site/style.css                   CSS propio (sin frameworks ni CDNs, salvo Google Fonts)
site/assets/logo-derenzin.png    Logo real de derenzin.com (mismo archivo, sin modificar)
site/assets/favicon.png          Favicon (mismo archivo que usa derenzin.com)
site/imagenes/AAAA-MM-DD/*.jpg   Imágenes de noticias descargadas y alojadas localmente
site/CNAME                       Dominio personalizado para GitHub Pages
site/archivo/AAAA-MM-DD.html     Una página por cada día publicado
site/archivo/index.html          Índice de todas las ediciones archivadas
.github/workflows/diario.yml     Automatización (cron diario + botón manual)
```

---

## Traducción al español (DeepL)

Todo el sitio se muestra en español — títulos, resúmenes y textos de
interfaz — aunque la fuente original esté en inglés (The Hacker News,
BleepingComputer, Krebs on Security, Dark Reading). Esto se hace con la
[API de DeepL](https://www.deepl.com/pro-api), llamada desde
`scripts/build_site.py` con la librería estándar de Python (`urllib`), sin
dependencias nuevas que instalar.

**Solo se traduce el texto que ya viene del RSS** (título + extracto): la
traducción nunca agrega cifras, nombres ni hechos que no estén en el
original. Si la traducción falla (sin clave configurada, red caída, cuota
mensual agotada, timeout), esa noticia en particular se muestra citando el
título/extracto original entre comillas con una nota aclaratoria — nunca se
fabrica una traducción alternativa.

### Cómo obtener y configurar la clave

1. Crea una cuenta gratuita en https://www.deepl.com/pro-api (el plan
   "DeepL API Free" incluye 500 000 caracteres/mes gratis, de sobra para el
   volumen diario de este sitio).
2. Copia tu "Authentication Key" (termina en `:fx` si es del plan gratuito;
   el script detecta esto solo y usa el endpoint correcto de DeepL).
3. En GitHub, ve a **Settings → Secrets and variables → Actions → New
   repository secret**.
4. Nombre del secreto: `DEEPL_API_KEY`. Valor: pega tu clave. **Guardar**.

No hace falta ningún otro cambio: el workflow ya está preparado para leer
`secrets.DEEPL_API_KEY` (ver `.github/workflows/diario.yml`) y pasarla como
variable de entorno a `build_site.py`.

**Si no configuras este secreto**, el sitio sigue funcionando: las noticias en
inglés se publican citando el título/extracto original entre comillas con la
nota "No se pudo traducir automáticamente (se muestra el original)", en vez
de fallar o inventar una traducción.

---

## Imágenes

Cada noticia muestra una imagen, con estas reglas para no romper la regla de
"solo contenido real":

### Diagnóstico del hotlinking original (por qué se cambió)

La primera versión enlazaba directo a la URL de imagen del RSS (p.ej. a
`blogger.googleusercontent.com` para The Hacker News). Al reportarse
imágenes rotas, se probó con `fetch()`, carga forzada de `<img>` y `curl`
directo (con y sin `Referer`, con distintos `User-Agent`) contra las mismas
URLs: **todas devolvieron HTTP 200 con el contenido completo** — no se pudo
reproducir un bloqueo de CORS, "mixed content" ni hotlinking del origen en
ese momento. La causa más probable es del lado del visitante (bloqueadores
de anuncios o filtros de red corporativos que bloquean dominios tipo
`googleusercontent.com`) o un problema intermitente del CDN de origen. De
cualquier forma, depender de un servidor de terceros que no está pensado
para servir estas imágenes es frágil por diseño — por eso se cambió al
enfoque de descarga local de todos modos.

### Cómo funciona ahora

- **Si el ítem del RSS trae una imagen propia** (etiqueta `<enclosure>` o
  `<media:content>`/`<media:thumbnail>`), `scripts/fetch_news.py` la
  **descarga** durante el proceso diario y la guarda en
  `site/imagenes/AAAA-MM-DD/<hash>.ext` (nombre de archivo = hash SHA-1 de
  la URL original, para evitar colisiones entre fuentes que reutilizan
  nombres genéricos como `micro.jpg`). El HTML apunta a esa copia local
  (`/imagenes/AAAA-MM-DD/...`), nunca a la URL externa.
  - Se valida el `Content-Type` de la respuesta (debe empezar con `image/`)
    y se descarta cualquier archivo mayor a 5MB.
  - Cada imagen descargada se registra en `data/imagenes_descargadas.json`
    (URL original → ruta local). Si la misma URL de imagen reaparece (poco
    frecuente, pero posible), se reutiliza el archivo ya descargado en vez
    de volver a bajarlo.
  - Si la descarga falla por cualquier motivo (timeout, HTTP distinto de
    200, tipo de contenido inválido, archivo demasiado pesado), el ítem
    queda sin imagen y se usa el ícono de categoría de respaldo — nunca se
    sustituye por otra imagen ni se deja un enlace roto.
  - No se re-codifican ni redimensionan las imágenes (para no depender de
    una librería pesada como Pillow) — se guardan tal cual las entrega la
    fuente.
  - A propósito, **no se exige que el dominio de la imagen coincida** con el
    dominio declarado de la fuente en `feeds.yaml` (a diferencia del enlace
    del artículo, que sí se valida estrictamente) — muchos feeds legítimos
    sirven sus imágenes desde un CDN distinto. La imagen sigue atada al
    ítem de RSS ya verificado; solo se exige un `Content-Type` de imagen.
- **Si el RSS no trae ninguna imagen estructurada, o la descarga falla**, se
  muestra un ícono SVG genérico (dibujado a mano, en línea en el HTML — no
  genera ninguna petición de red) según la categoría de la noticia:
  `ransomware`, `phishing`, `filtración de datos`, `vulnerabilidad`,
  `malware`, `ataque DDoS`, o un ícono genérico de "ciberseguridad" si no
  coincide ninguna categoría. La categoría se detecta por palabras clave en
  el título/extracto original (ver `categorizar()` en
  `scripts/build_site.py`) — nunca se le pide a una IA que genere ni
  imagine una foto del hecho. El ícono siempre lleva una leyenda visible
  tipo "Ilustración genérica: Ransomware (no es una foto real del hecho)".
- **Toda imagen (real o ícono) lleva una insignia de color** con el nombre
  de la categoría superpuesta en la esquina — se ve tanto en la noticia
  destacada como en cada tarjeta de la cuadrícula.
  - Nota: los `<img>` incrustados dentro del cuerpo HTML de algunos RSS
    (p.ej. INCIBE-CERT) se ignoran a propósito — al revisarlos, resultaron
    ser botones de "compartir en redes sociales", no imágenes de la noticia.

**Nota sobre el tamaño del repositorio**: cada imagen descargada queda en el
repo permanentemente (necesario para que GitHub Pages la sirva). Con el
tiempo esto hace crecer el repositorio unos pocos MB por día — no se
implementó limpieza automática de imágenes viejas; si en el futuro quieres
una política de retención (p.ej. borrar imágenes de ediciones de más de N
meses), es un cambio aparte, avísame.

---

## Identidad visual (consistente con derenzin.com)

El diseño toma la paleta y los activos reales de https://derenzin.com (se
visitó el sitio en vivo para extraerlos, no se inventaron):

- **Logo**: `site/assets/logo-derenzin.png` es el mismo archivo que usa
  derenzin.com (`img/logo-derenzin.png`), sin modificar. También se reutiliza
  como favicon (`site/assets/favicon.png`, igual que en el sitio original).
- **Color de marca**: `#00B8D4` (la variable `--secondary-color` del CSS de
  derenzin.com) es el acento principal — se usa en encabezados grandes,
  bordes, íconos y la noticia destacada de portada. Para los **enlaces de
  texto normal** se usa `#007A94` (misma familia de cian, más oscuro) porque
  el cian puro (`#00B8D4`) no alcanza el contraste mínimo legible (AA, 4.5:1)
  sobre fondo blanco — sí lo alcanza sobre fondo oscuro, así que en modo
  oscuro los enlaces usan el cian puro. Es una decisión deliberada de
  accesibilidad, manteniendo el mismo tono de marca.
- **Pie de página**: siempre oscuro (`#1A1A1A`) con texto claro, igual que el
  footer de derenzin.com, independientemente del tema claro/oscuro del resto
  del sitio — incluye el logo, un enlace a https://derenzin.com, el aviso
  legal de agregación de noticias, y el copyright (`© AAAA DERENZIN S.A.S. —
  Feddor Derenzin Martínez...`, tomado del mismo texto que usa el sitio
  oficial, con el año calculado automáticamente en cada build).
- **Modo oscuro/claro**: automático según la preferencia del sistema
  operativo del visitante (`prefers-color-scheme`), sin necesidad de un
  interruptor manual ni JavaScript adicional.

---

## Diseño (portal de noticias profesional)

- **Tipografía**: [Inter](https://fonts.google.com/specimen/Inter) de Google
  Fonts (pesos 400 a 800), con la pila de sistema de derenzin.com como
  respaldo si Google Fonts no carga (`"Segoe UI", -apple-system, ...`). Es
  la única dependencia externa del sitio, aparte del logo (que ya es propio).
- **Cabecera fija**: el header queda pegado arriba (`position: sticky`) con
  el logo, "Inicio" y "Archivo".
- **Noticia destacada**: la más reciente del día se muestra arriba en
  grande — imagen ancha (proporción 21:9 en escritorio, 16:9 en celular),
  título grande, resumen más largo y un botón de "Leer la noticia completa".
- **Cuadrícula de tarjetas**: el resto de noticias del día se muestra en un
  grid responsive (`repeat(auto-fill, minmax(270px, 1fr))`) — 3 columnas en
  escritorio, 2 en tablet, **1 columna en celular** (sin media queries
  manuales para esto: el propio `auto-fill` colapsa solo). Cada tarjeta
  tiene esquinas redondeadas, sombra sutil, y al pasar el mouse se eleva
  ligeramente y la imagen hace un zoom suave (`transform: scale()`).
- **Insignia de categoría**: cada imagen (real o ícono) lleva una etiqueta
  de color en la esquina superior (Ransomware, Phishing, Vulnerabilidad,
  Malware, Filtración de datos, DDoS, o Ciberseguridad genérica).
- **Resúmenes recortados visualmente**: con `-webkit-line-clamp` se cortan a
  3-4 líneas en pantalla, sin cortar el texto real en el HTML (accesible
  para lectores de pantalla y buscadores igual).
- Inspirado en la jerarquía de portales como BleepingComputer/The Hacker
  News (destacada arriba + grid abajo) — sin copiar su marca ni su CSS.

---

## Cómo funciona el workflow (.github/workflows/diario.yml)

1. **Cron diario** a las `11:00 UTC` (≈ 06:00 America/Guayaquil, UTC-5 todo el
   año) + botón manual (`workflow_dispatch`) para probarlo cuando quieras desde
   la pestaña *Actions* de GitHub.
2. Instala Python + las dos dependencias mínimas (`feedparser`, `PyYAML`).
3. Corre `fetch_news.py` y luego `build_site.py` (este último recibe
   `DEEPL_API_KEY` desde los Secrets del repo, si lo configuraste).
4. Si hubo noticias nuevas, commitea `site/` (incluidas las imágenes
   descargadas), `data/publicadas.json` y `data/imagenes_descargadas.json` a
   la rama `main` con el usuario `github-actions[bot]`.
5. Publica el contenido de `site/` en la rama `gh-pages` (así GitHub Pages lo
   puede servir con "Deploy from a branch", que solo soporta la raíz o
   `/docs` de una rama — no una carpeta arbitraria como `/site` dentro de
   `main`). Esto se hace con comandos `git` normales (worktree + rama
   huérfana la primera vez), sin ninguna acción de terceros ni permisos
   adicionales — solo `contents: write`.
6. Si ningún feed trajo novedades, el job igual termina exitosamente (verde);
   simplemente no hay commit ese día.

La única clave/API externa que usa el proyecto es `DEEPL_API_KEY`, exclusiva
para traducir al español (ver arriba). Sin ella configurada, el sitio sigue
funcionando en modo seguro (cita el original en vez de traducir).

---

## Cómo probarlo en local antes de confiar en el cron

Requisitos: Python 3.11+ (en este equipo se usó `py -3`, ajusta según tu
sistema).

```bash
pip install -r requirements.txt
python scripts/fetch_news.py

# Sin DEEPL_API_KEY: las noticias en inglés se publican citando el original.
python scripts/build_site.py

# Con traducción real (opcional, si ya tienes una clave de DeepL):
#   macOS/Linux:  DEEPL_API_KEY="tu-clave:fx" python scripts/build_site.py
#   Windows PowerShell:  $env:DEEPL_API_KEY="tu-clave:fx"; python scripts/build_site.py
```

- `fetch_news.py` deja un log en pantalla de qué fuentes procesó, cuántos
  ítems nuevos encontró en cada una, y por qué descartó los que descartó
  (dominio no coincidente, fuera de la ventana de 24-48h, ya publicado antes,
  etc.). Genera/actualiza `data/nuevas_hoy.json`.
- `build_site.py` lee ese archivo y genera `site/index.html`,
  `site/archivo/AAAA-MM-DD.html` y actualiza `data/publicadas.json`. Si
  `nuevas_hoy.json` está vacío, no toca nada y lo deja dicho en el log.
- Para ver el resultado en el navegador sin depender de GitHub Pages:
  ```bash
  cd site
  python -m http.server 8000
  # abre http://localhost:8000/index.html
  ```
- Para simular un segundo día sin duplicar noticias, simplemente vuelve a
  correr ambos scripts: los ítems que ya están en `data/publicadas.json` se
  descartan automáticamente.
- Si quieres reiniciar el ledger para probar "desde cero", basta con dejar
  `data/publicadas.json` como `{"urls": {}}` y borrar `data/nuevas_hoy.json`.

Este flujo ya se probó de punta a punta (las 6 fuentes, filtro de dominio,
deduplicación, extracción de imágenes reales vs. ícono genérico por
categoría, el modo seguro de traducción sin clave, el parseo de una
respuesta simulada de DeepL, y la lógica de publicar en la rama `gh-pages`
con una rama huérfana simulada) antes de entregarte este repositorio.

---

## Pasos manuales que te faltan a ti

### 1. Crear el repositorio en GitHub y subir el código

Como no tengo el CLI `gh` disponible en este equipo para crearlo por ti, hazlo
así (reemplaza la URL si prefieres SSH):

```bash
git init
git add -A
git commit -m "Setup inicial del periódico de ciberseguridad"
git branch -M main
git remote add origin https://github.com/gderenzin/noticias.git
git push -u origin main
```

(Crea antes el repo vacío `gderenzin/noticias` en github.com/new — sin README,
sin .gitignore, sin licencia, para que el push no choque con nada.)

### 2. Activar GitHub Pages

El primer `push` a `main` no crea todavía la rama `gh-pages` — esa la crea el
workflow la primera vez que corra. Así que:

1. Ve a la pestaña **Actions** del repo y lanza el workflow **"Diario - Noticias
   de ciberseguridad"** manualmente (botón *Run workflow* → *Run workflow*).
   Esto crea la rama `gh-pages` con el contenido de `site/`.
2. Ve a **Settings → Pages**.
3. En **Source**, elige **Deploy from a branch**.
4. En **Branch**, elige **gh-pages** y la carpeta **/ (root)** → **Save**.
5. Más abajo, en **Custom domain**, escribe `noticias.derenzin.com` y guarda
   (esto ya viene precargado por el archivo `site/CNAME`, pero GitHub pide
   confirmarlo una vez desde la UI).
6. Cuando el DNS (paso 3) esté propagado, marca **Enforce HTTPS**.

### 3. Configurar el DNS en tu proveedor de dominio (derenzin.com)

Como usas un **subdominio** (`noticias.derenzin.com`), agrega un registro
**CNAME**, no registros A:

| Tipo | Host / Nombre | Valor / Destino | TTL |
|---|---|---|---|
| CNAME | `noticias` | `gderenzin.github.io` | Auto o 3600 |

(Si en vez de un subdominio quisieras usar el dominio raíz `derenzin.com`
directamente, en su lugar tendrías que crear 4 registros **A** apuntando a las
IPs de GitHub Pages: `185.199.108.153`, `185.199.109.153`, `185.199.110.153` y
`185.199.111.153` — pero con el subdominio que ya definiste, el CNAME de
arriba es lo único que necesitas.)

La propagación de DNS puede tardar desde minutos hasta un par de horas.
GitHub Pages emitirá el certificado HTTPS automáticamente una vez detecte el
CNAME correcto — ahí es cuando conviene volver a Settings → Pages y marcar
"Enforce HTTPS" si no se marcó solo.

### 4. Revisar el primer run real

Después del primer `workflow_dispatch` (paso 2.1), revisa en **Actions** que
el job haya quedado en verde, y abre `https://noticias.derenzin.com` (o,
mientras el DNS propaga, `https://gderenzin.github.io/noticias/`) para
confirmar que salieron noticias reales con sus enlaces.

### 5. (Opcional pero recomendado) Configurar la traducción al español

Sin este paso el sitio ya funciona — solo que las noticias en inglés se
muestran citando el original en vez de traducidas. Para traducción real, ve a
la sección **"Traducción al español (DeepL)"** más arriba: crea una cuenta
gratuita en DeepL y agrega tu clave como el secreto `DEEPL_API_KEY` en
**Settings → Secrets and variables → Actions** del repo.

### 6. Ajustar el horario del cron (opcional)

El cron vive en `.github/workflows/diario.yml`, en la línea `- cron: "0 11 * * *"`,
con comentarios al lado explicando la conversión a hora de Guayaquil. Está en
UTC porque así lo interpreta GitHub Actions siempre — ajusta ese valor si
quieres otra hora.

---

## Aviso legal fijo (aparece en cada página)

> Este sitio agrega titulares y resúmenes de fuentes públicas verificadas; el
> contenido pertenece a sus autores originales. Consulta el enlace de cada
> fuente para leer el artículo completo.
