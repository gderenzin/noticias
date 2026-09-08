// cabecera-sticky.js
// -----------------------------------------------------------------------
// La cabecera ya es position: sticky por CSS (no depende de este script
// para quedarse fija) -- esto SOLO agrega la clase "cabecera--compacta"
// cuando el usuario ya bajó un poco, para reducir su alto en pantalla
// mientras hace scroll (ver .cabecera--compacta en style.css). Sencillo
// a propósito: un solo listener de scroll, sin librerías.
(function () {
  "use strict";
  var cabecera = document.querySelector(".cabecera");
  if (!cabecera) return;

  var UMBRAL_PX = 24;
  var compacta = false;

  function actualizar() {
    var debeEstarCompacta = window.scrollY > UMBRAL_PX;
    if (debeEstarCompacta !== compacta) {
      compacta = debeEstarCompacta;
      cabecera.classList.toggle("cabecera--compacta", compacta);
    }
  }

  window.addEventListener("scroll", actualizar, { passive: true });
  actualizar();
})();
