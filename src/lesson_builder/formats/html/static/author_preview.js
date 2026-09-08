(() => {
  "use strict";
  document.querySelectorAll(".section-link").forEach((link) => {
    link.addEventListener("click", () => {
      const target = document.querySelector(link.getAttribute("href"));
      if (target instanceof HTMLElement) {
        window.requestAnimationFrame(() => target.focus({ preventScroll: true }));
      }
    });
  });
})();
