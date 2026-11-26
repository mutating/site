import { initComputeGlobe } from "./compute-globe.js";

// The lab shows several hero candidates on one page. Each visual is an
// independent instance so every planet can be dragged and animated.
(() => {
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  document.querySelectorAll("[data-hero-instance]").forEach((instance) => {
    initComputeGlobe(instance, motion);
  });
  document.querySelectorAll(".chl-index a").forEach((link) => {
    link.addEventListener("click", () => {
      history.replaceState(null, "", link.getAttribute("href"));
    });
  });
})();
