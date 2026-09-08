# Imágenes de respaldo para og:image (por categoría)

Estos 8 PNG (uno por categoría, más `generico.png`) son la versión
**rasterizada** de los íconos de `site/assets/iconos/*.svg`, usada
específicamente como respaldo de `og:image` en las páginas de detalle de
noticia cuando el artículo no tiene una imagen real.

**Por qué no se usa el `.svg` directamente:** WhatsApp/Facebook (y la
mayoría de crawlers de Open Graph) no renderizan SVG como `og:image` — solo
formatos rasterizados (PNG/JPEG). Usar el `.svg` ahí mostraría la vista
previa en blanco en vez de mostrar el ícono de categoría.

**Cómo se generaron** (una sola vez, no forma parte del workflow diario):
para cada ícono, se armó un SVG de 1200×630 con un rectángulo de fondo del
color de la categoría (mismos valores que `CATEGORIAS[...]["color"]` en
`scripts/build_site.py`) y el dibujo del ícono original centrado, recoloreado
a blanco (`stroke="#ffffff"`, `fill="none"`, igual que el original pero
invertido para que se vea sobre el fondo sólido). Ese SVG compuesto se
rasterizó a PNG vía un `<canvas>` en un navegador real (sin depender de
ninguna librería de conversión SVG→PNG en el servidor).

Si se agrega una categoría nueva en el futuro, hay que generar su PNG acá
con el mismo procedimiento (o pedirle a Claude que lo repita).
