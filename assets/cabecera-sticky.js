// cabecera-sticky.js
// -----------------------------------------------------------------------
// La cabecera ya es position: sticky por CSS (no depende de este script
// para quedarse fija) -- esto SOLO agrega la clase "cabecera--compacta"
// cuando el usuario ya bajó un poco, para reducir su alto en pantalla
// mientras hace scroll (ver .cabecera--compacta en style.css).
//
// Bug real encontrado y corregido: compactar la cabecera reduce su alto
// real (~60px menos de padding/contenido), lo que desplaza todo el
// contenido de abajo y puede modificar scrollY -- con un solo umbral, ese
// desplazamiento podía volver a cruzarlo en sentido contrario, generando
// un ciclo de activación/desactivación en bucle ("jitter") justo en esa
// zona de scroll. Dos correcciones, pensadas para eliminarlo de raíz, no
// solo disimularlo:
//
// 1. Histéresis (patrón Schmitt trigger): dos umbrales distintos, uno
//    para activar y otro (más bajo) para desactivar, con un margen entre
//    ambos (120px) mayor que el cambio de alto real de la cabecera
//    (~60px en escritorio, ~90px en mobile, donde el título ocupa dos
//    líneas en el estado normal -- medido en ambos con DevTools Protocol)
//    -- así ese desplazamiento nunca alcanza por sí solo a cruzar el
//    umbral contrario, sin importar en qué dirección se dé ni el ancho
//    de pantalla.
// 2. Un solo recálculo por frame (requestAnimationFrame), nunca uno por
//    cada evento de scroll individual -- evita reaccionar sobre un
//    scrollY todavía "en tránsito" por el propio cambio de layout de la
//    corrida anterior.
(function () {
  "use strict";
  var cabecera = document.querySelector(".cabecera");
  if (!cabecera) return;

  var UMBRAL_ACTIVAR = 140; // scrollY > esto -> pasa a compacta
  var UMBRAL_DESACTIVAR = 20; // scrollY < esto -> vuelve a completa
  var compacta = false;
  var actualizacionProgramada = false;

  function actualizar() {
    actualizacionProgramada = false;
    var y = window.scrollY;
    if (!compacta && y > UMBRAL_ACTIVAR) {
      compacta = true;
      cabecera.classList.add("cabecera--compacta");
    } else if (compacta && y < UMBRAL_DESACTIVAR) {
      compacta = false;
      cabecera.classList.remove("cabecera--compacta");
    }
    // Entre ambos umbrales: se deja el estado como está (zona muerta de
    // la histéresis), a propósito -- es justo lo que evita el bucle.
  }

  function alScrollear() {
    if (actualizacionProgramada) return;
    actualizacionProgramada = true;
    window.requestAnimationFrame(actualizar);
  }

  window.addEventListener("scroll", alScrollear, { passive: true });
  actualizar();
})();
