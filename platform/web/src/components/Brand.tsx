import { useEffect, useRef } from "react";

export function Brand({ href }: { href: string }) {
  const brandRef = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    const brand = brandRef.current!;
    const header = brand.closest<HTMLElement>(".site-header")!;
    const eye = brand.querySelector<HTMLElement>(".brand-eye")!;
    const pupil = brand.querySelector<HTMLElement>(".brand-pupil")!;
    const stage = brand.querySelector<HTMLElement>(".brand-wordmark-stage")!;
    const wordmark = brand.querySelector<HTMLElement>(".wordmark")!;
    const actions = header.querySelector<HTMLElement>(".header-actions")!;
    const caption = header.querySelector<HTMLElement>(".header-caption")!;
    const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
    let disposed = false;
    let gazeFrame = 0;
    let layoutFrame = 0;
    let previousFrameAt = 0;
    let pointer: { x: number; y: number } | null = null;
    const gaze = {
      x: 0,
      y: 0,
      vx: 0,
      vy: 0,
      targetX: 0,
      targetY: 0,
      maxX: 1,
      maxY: 1,
    };

    const renderGaze = () => {
      pupil.style.setProperty("--gaze-x", `${gaze.x.toFixed(2)}px`);
      pupil.style.setProperty("--gaze-y", `${gaze.y.toFixed(2)}px`);
      pupil.style.setProperty(
        "--gaze-tilt",
        `${((-gaze.x / gaze.maxX) * 2.8).toFixed(2)}deg`,
      );
    };
    const animateGaze = (now: number) => {
      gazeFrame = 0;
      let remaining = Math.min(
        4,
        previousFrameAt ? (now - previousFrameAt) / (1000 / 60) : 1,
      );
      previousFrameAt = now;
      while (remaining > 0.0001) {
        const step = Math.min(1, remaining);
        const damping = 0.79 ** step;
        gaze.vx = (gaze.vx + (gaze.targetX - gaze.x) * 0.068 * step) * damping;
        gaze.vy = (gaze.vy + (gaze.targetY - gaze.y) * 0.068 * step) * damping;
        gaze.x += gaze.vx * step;
        gaze.y += gaze.vy * step;
        const ellipse = (gaze.x / gaze.maxX) ** 2 + (gaze.y / gaze.maxY) ** 2;
        if (ellipse > 1) {
          const scale = 1 / Math.sqrt(ellipse);
          gaze.x *= scale;
          gaze.y *= scale;
          gaze.vx *= 0.35;
          gaze.vy *= 0.35;
        }
        remaining -= step;
      }
      const moving =
        Math.abs(gaze.x - gaze.targetX) +
          Math.abs(gaze.y - gaze.targetY) +
          Math.abs(gaze.vx) +
          Math.abs(gaze.vy) >
        0.005;
      if (!moving) {
        gaze.x = gaze.targetX;
        gaze.y = gaze.targetY;
        gaze.vx = gaze.vy = 0;
      }
      renderGaze();
      if (moving) gazeFrame = requestAnimationFrame(animateGaze);
      else previousFrameAt = 0;
    };
    const scheduleGaze = () => {
      if (reducedMotion.matches) {
        cancelAnimationFrame(gazeFrame);
        gazeFrame = previousFrameAt = 0;
        gaze.x = gaze.targetX;
        gaze.y = gaze.targetY;
        gaze.vx = gaze.vy = 0;
        renderGaze();
      } else if (!gazeFrame) gazeFrame = requestAnimationFrame(animateGaze);
    };
    const aimAtPointer = () => {
      if (!pointer) {
        gaze.targetX = gaze.targetY = 0;
      } else {
        const rect = eye.getBoundingClientRect();
        const dx = pointer.x - (rect.left + rect.width / 2);
        const dy = pointer.y - (rect.top + rect.height / 2);
        const distance = Math.hypot(dx, dy);
        if (distance) {
          const ux = dx / distance;
          const uy = dy / distance;
          const boundary =
            1 / Math.sqrt((ux / gaze.maxX) ** 2 + (uy / gaze.maxY) ** 2);
          const reach = Math.min(
            1,
            distance / (Math.min(innerWidth, innerHeight) * 0.34 + 40),
          );
          gaze.targetX = ux * boundary * reach;
          gaze.targetY = uy * boundary * reach;
        } else gaze.targetX = gaze.targetY = 0;
      }
      scheduleGaze();
    };
    const synchronizeExpansion = () => {
      if (!pointer || stage.dataset.canExpand !== "true") {
        delete wordmark.dataset.hoverExpanded;
        return;
      }
      // Animated letters move beneath a stationary cursor. Physical pointer motion
      // starts expansion on TMC, then the fixed stage retains it (as on the site).
      const target =
        wordmark.dataset.hoverExpanded === "true" ? stage : wordmark;
      const rect = target.getBoundingClientRect();
      if (
        pointer.x >= rect.left &&
        pointer.x < rect.right &&
        pointer.y >= rect.top &&
        pointer.y < rect.bottom
      ) {
        wordmark.dataset.hoverExpanded = "true";
      } else delete wordmark.dataset.hoverExpanded;
    };
    const updateLayout = () => {
      layoutFrame = 0;
      const eyeRect = eye.getBoundingClientRect();
      gaze.maxX = Math.max(1, eyeRect.width * 0.255);
      gaze.maxY = Math.max(1, eyeRect.height * 0.142);
      const headerStyle = getComputedStyle(header);
      const brandStyle = getComputedStyle(brand);
      const number = (value: string) => Number.parseFloat(value) || 0;
      const captionWidth = caption.getBoundingClientRect().width;
      const available =
        header.clientWidth -
        number(headerStyle.paddingLeft) -
        number(headerStyle.paddingRight) -
        actions.getBoundingClientRect().width -
        captionWidth -
        number(headerStyle.columnGap) * (captionWidth ? 2 : 1) -
        eyeRect.width -
        number(brandStyle.columnGap);
      const expandedWidth = number(getComputedStyle(stage).fontSize) * 11.6;
      stage.dataset.canExpand =
        available + 0.5 >= expandedWidth ? "true" : "false";
      for (const reveal of wordmark.querySelectorAll<HTMLElement>(
        ".brand-word-reveal",
      )) {
        const range = document.createRange();
        range.selectNodeContents(reveal);
        reveal.style.setProperty(
          "--reveal-width",
          `${range.getBoundingClientRect().width}px`,
        );
      }
      synchronizeExpansion();
      aimAtPointer();
    };
    const scheduleLayout = () => {
      if (!layoutFrame && !disposed)
        layoutFrame = requestAnimationFrame(updateLayout);
    };
    const onPointer = (event: PointerEvent) => {
      pointer = { x: event.clientX, y: event.clientY };
      aimAtPointer();
      synchronizeExpansion();
    };
    const resetPointer = () => {
      pointer = null;
      synchronizeExpansion();
      aimAtPointer();
    };
    const onPointerOut = (event: PointerEvent) => {
      if (!event.relatedTarget) resetPointer();
    };
    const onFocus = () => {
      if (brand.matches(":focus-visible"))
        wordmark.dataset.focusExpanded = "true";
    };
    const onBlur = () => {
      delete wordmark.dataset.focusExpanded;
    };
    const observer = new ResizeObserver(scheduleLayout);
    for (const element of [header, actions, caption, eye])
      observer.observe(element);
    document.addEventListener("pointermove", onPointer, {
      passive: true,
      capture: true,
    });
    document.addEventListener("pointerdown", onPointer, {
      passive: true,
      capture: true,
    });
    document.addEventListener("pointerout", onPointerOut);
    window.addEventListener("blur", resetPointer);
    window.addEventListener("resize", scheduleLayout, { passive: true });
    window.addEventListener("scroll", aimAtPointer, { passive: true });
    reducedMotion.addEventListener("change", scheduleGaze);
    brand.addEventListener("focus", onFocus);
    brand.addEventListener("blur", onBlur);
    document.fonts.ready.then(scheduleLayout);
    scheduleLayout();
    return () => {
      disposed = true;
      cancelAnimationFrame(gazeFrame);
      cancelAnimationFrame(layoutFrame);
      observer.disconnect();
      document.removeEventListener("pointermove", onPointer, true);
      document.removeEventListener("pointerdown", onPointer, true);
      document.removeEventListener("pointerout", onPointerOut);
      window.removeEventListener("blur", resetPointer);
      window.removeEventListener("resize", scheduleLayout);
      window.removeEventListener("scroll", aimAtPointer);
      reducedMotion.removeEventListener("change", scheduleGaze);
      brand.removeEventListener("focus", onFocus);
      brand.removeEventListener("blur", onBlur);
    };
  }, []);

  return (
    <a
      ref={brandRef}
      className="brand"
      href={href}
      tabIndex={0}
      aria-label="The Mutating Company"
    >
      <span className="brand-eye" aria-hidden="true">
        <span className="brand-pupil" />
      </span>
      <span
        className="brand-wordmark-stage"
        data-can-expand="false"
        aria-hidden="true"
      >
        <span className="wordmark">
          {[
            ["T", "he"],
            ["M", "utating"],
            ["C", "ompany"],
          ].map(([anchor, reveal]) => (
            <span className="brand-word-part" key={anchor}>
              <span className="brand-word-anchor">{anchor}</span>
              <span className="brand-word-reveal" data-text={reveal}>
                {reveal}
              </span>
            </span>
          ))}
        </span>
      </span>
    </a>
  );
}
