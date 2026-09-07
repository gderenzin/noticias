// Boton "Copiar enlace" de los botones de compartir (ver
// render_botones_compartir en scripts/build_site.py). Los enlaces a
// WhatsApp/X/Facebook/LinkedIn/Telegram son <a href> normales y no
// necesitan JS -- esto solo cubre copiar al portapapeles con confirmacion
// breve, sin ninguna libreria ni dependencia externa.
(function () {
  "use strict";

  function copiarEnlace(boton) {
    var url = boton.getAttribute("data-url");
    var msg = boton.querySelector(".compartir-copiar-msg");
    if (!url) return;

    function mostrarConfirmacion(texto) {
      if (!msg) return;
      msg.textContent = texto;
      boton.classList.add("compartir-copiar--hecho");
      window.setTimeout(function () {
        msg.textContent = "";
        boton.classList.remove("compartir-copiar--hecho");
      }, 2000);
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(
        function () {
          mostrarConfirmacion("¡Copiado!");
        },
        function () {
          mostrarConfirmacion("No se pudo copiar");
        }
      );
    } else {
      // Respaldo para navegadores muy viejos sin navigator.clipboard.
      var campo = document.createElement("textarea");
      campo.value = url;
      campo.setAttribute("readonly", "");
      campo.style.position = "absolute";
      campo.style.left = "-9999px";
      document.body.appendChild(campo);
      campo.select();
      try {
        document.execCommand("copy");
        mostrarConfirmacion("¡Copiado!");
      } catch (e) {
        mostrarConfirmacion("No se pudo copiar");
      }
      document.body.removeChild(campo);
    }
  }

  document.addEventListener("click", function (ev) {
    var boton = ev.target.closest && ev.target.closest(".compartir-copiar");
    if (boton) copiarEnlace(boton);
  });
})();
