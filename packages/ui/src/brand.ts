import { createScope, type Cleanup } from "./internal";
import { THEME_TOGGLE_EVENT } from "./theme";

export const BRAND_HOME_ACTIVATE_EVENT = "tmc-brand-home-activate";
export const BRAND_POINTER_EVENT = "tmc-tentacle-pointer-move";
const THEME_TOGGLE_BLINK_EVENT = THEME_TOGGLE_EVENT;
const BRAND_HOME_RELOAD_DELAY_MS = 260;

export interface BrandOptions {
  expandWordmark?: boolean;
  /** Also unfold on keyboard focus; disabled by default to preserve the static site. */
  expandOnFocus?: boolean;
  /** Fit the real expanded text and overflowing header actions at every viewport. Defaults to false. */
  fitWordmarkToHeader?: boolean;
  /** Follow eye layout and motion preference changes, including reduced-motion resets. Defaults to false. */
  reactiveGaze?: boolean;
  scrollTone?: boolean;
  blinkIntervalMs?: number;
  /** Reload the current home after its closing-eye animation, as on mutating.tech. */
  activation?: "navigate" | "reload-home";
}

function environment(brand: HTMLElement) {
  const scope = createScope(brand);
  const { window, document } = scope;
  const frames = new Set<number>();
  const on = <E extends Event>(target: EventTarget, type: string, listener: (event: E) => void, options: AddEventListenerOptions = {}) => {
    scope.on(target, type, listener, options);
  };
  const requestAnimationFrame = (callback: FrameRequestCallback) => {
    if (scope.disposed) return 0;
    const id = window.requestAnimationFrame((now) => { frames.delete(id); if (!scope.disposed) callback(now); });
    frames.add(id);
    return id;
  };
  const cancelAnimationFrame = (id: number) => { window.cancelAnimationFrame(id); frames.delete(id); };
  // Resolve the native API when an observer is actually created. Defining an
  // inherited class eagerly would fail before the original pointer listeners.
  class ResizeObserver {
    private readonly observer: globalThis.ResizeObserver;
    constructor(callback: ResizeObserverCallback) {
      this.observer = new window.ResizeObserver(callback);
      scope.add(() => this.observer.disconnect());
    }
    observe(target: Element, options?: ResizeObserverOptions) { this.observer.observe(target, options); }
    unobserve(target: Element) { this.observer.unobserve(target); }
    disconnect() { this.observer.disconnect(); }
  }
  const setInterval = (callback: () => void, delay: number) => {
    const id = window.setInterval(callback, delay); scope.add(() => window.clearInterval(id)); return id;
  };
  const setTimeout = (callback: () => void, delay: number) => {
    const id = window.setTimeout(callback, delay); scope.add(() => window.clearTimeout(id)); return id;
  };
  scope.add(() => frames.forEach((id) => window.cancelAnimationFrame(id)));
  return {
    window, document, on, requestAnimationFrame, cancelAnimationFrame, ResizeObserver, setInterval, setTimeout,
    matchMedia: window.matchMedia.bind(window), getComputedStyle: window.getComputedStyle.bind(window),
    performance: window.performance, CSS: window.CSS, disposed: () => scope.disposed, destroy: scope.destroy,
  };
}
type Environment = ReturnType<typeof environment>;

/** Uses the original site's geometry and animation equations with instance-owned cleanup. */
export function mountBrand(brand: HTMLElement, options: BrandOptions = {}): Cleanup {
  const env = environment(brand);
  let declared: BrandOptions = {};
  try { declared = JSON.parse(brand.dataset.tmcBrandOptions || "{}"); } catch { /* Ignore malformed optional configuration. */ }
  const animation = { ...declared, ...options };
  mountWordmark(brand, animation, env);
  mountBrandActivation(brand, animation, env);
  mountEye(brand, animation, env);
  return () => {
    if (env.disposed()) return;
    env.destroy();
    const stage = brand.querySelector<HTMLElement>(".tmc-brand__wordmark-stage");
    const wordmark = brand.querySelector<HTMLElement>(".tmc-brand__wordmark");
    if (stage) { delete stage.dataset.canExpand; delete stage.dataset.expansionMeasured; delete stage.dataset.expansionRevision; delete stage.dataset.fitToHeader; }
    if (wordmark) { delete wordmark.dataset.hoverExpanded; delete wordmark.dataset.focusExpanded; }
    for (const element of brand.querySelectorAll<HTMLElement>(".tmc-eye, .tmc-eye__clip, .tmc-eye__pupil")) {
      for (const name of ["--eye-scroll-color", "--eye-scroll-outline-opacity", "--gaze-x", "--gaze-y", "--gaze-tilt", "clip-path", "-webkit-clip-path"]) element.style.removeProperty(name);
    }
  };
}

  function mountWordmark(brand: HTMLElement, options: BrandOptions, env: Environment) {
    const { document, window, on, requestAnimationFrame, cancelAnimationFrame, ResizeObserver, matchMedia, getComputedStyle } = env;
    const stage = brand.querySelector<HTMLElement>(".tmc-brand__wordmark-stage");
    const header = stage?.closest<HTMLElement>(".tmc-header");
    const actions = header?.querySelector<HTMLElement>(".tmc-header-actions");
    const eye = brand?.querySelector<HTMLElement>(".tmc-brand__mark--eye");
    const wordmark = stage?.querySelector<HTMLElement>(".tmc-brand__wordmark");
    if (!stage || !eye || !wordmark) return;
    if (options.fitWordmarkToHeader === true) stage.dataset.fitToHeader = "true";
    if (options.expandOnFocus && options.expandWordmark !== false) {
      const expandForFocus = () => {
        if (brand.matches(":focus-visible")) wordmark.dataset.focusExpanded = "true";
      };
      on(brand, "focus", expandForFocus);
      on(brand, "blur", () => { delete wordmark.dataset.focusExpanded; });
      expandForFocus();
    }
    if (!header || !actions) {
      stage.dataset.canExpand = options.expandWordmark === false ? "false" : "true";
      on(brand, "pointerenter", () => { if (stage.dataset.canExpand === "true") wordmark.dataset.hoverExpanded = "true"; });
      on(brand, "pointerleave", () => { delete wordmark.dataset.hoverExpanded; });
      return;
    }
    let frameRequest = 0;
    let lastPointer: { x: number; y: number } | null = null;

    const containsPoint = (element: HTMLElement, x: number, y: number) => {
      const rect = element.getBoundingClientRect();
      return (
        x >= rect.left &&
        x < rect.right &&
        y >= rect.top &&
        y < rect.bottom
      );
    };

    const setPointerExpansion = (expanded: boolean) => {
      if (expanded) {
        if (wordmark.dataset.hoverExpanded !== "true") {
          wordmark.dataset.hoverExpanded = "true";
        }
      } else if (wordmark.hasAttribute("data-hover-expanded")) {
        delete wordmark.dataset.hoverExpanded;
      }
    };

    const synchronizePointerExpansion = (x: number, y: number) => {
      lastPointer = { x, y };
      if (stage.dataset.canExpand !== "true") {
        setPointerExpansion(false);
        return;
      }
      const hitTarget =
        wordmark.dataset.hoverExpanded === "true" ? stage : wordmark;
      setPointerExpansion(containsPoint(hitTarget, x, y));
    };

    const publishExpansionAvailability = (canExpand: boolean) => {
      const becameExpandable =
        canExpand && stage.dataset.canExpand !== "true";
      stage.dataset.canExpand = canExpand ? "true" : "false";
      if (!canExpand) {
        setPointerExpansion(false);
      } else if (lastPointer) {
        synchronizePointerExpansion(lastPointer.x, lastPointer.y);
      } else if (becameExpandable && wordmark.matches(":hover")) {
        setPointerExpansion(true);
      }
    };

    // Pointer boundary events can be synthesized when an animated hit box
    // moves under a stationary cursor. Only physical pointer motion may change
    // this state: compact TMC starts it, then the fixed stage retains it.
    on(document, 
      "pointermove",
      (event: PointerEvent) => {
        synchronizePointerExpansion(event.clientX, event.clientY);
      },
      { passive: true, capture: true },
    );
    on(document, "pointerout", (event: PointerEvent) => {
      if (event.relatedTarget !== null) return;
      lastPointer = null;
      setPointerExpansion(false);
    });
    on(window, "blur", () => {
      lastPointer = null;
      setPointerExpansion(false);
    });
    if (
      stage.dataset.canExpand === "true" &&
      wordmark.matches(":hover")
    ) {
      setPointerExpansion(true);
    }

    const measureExpandedWidth = () => {
      // Measure the final CSS geometry without changing the visible wordmark or
      // borrowing its intermediate animation frame. Keep inherited fonts and
      // user text spacing by placing the hidden probe next to the real stage.
      const probe = stage.cloneNode(true) as HTMLElement;
      probe.dataset.canExpand = "true";
      probe.querySelector<HTMLElement>(".tmc-brand__wordmark")!.dataset.hoverExpanded = "true";
      probe.style.cssText += ";position:fixed!important;inset:0 auto auto 0!important;visibility:hidden!important;pointer-events:none!important;width:max-content!important;min-width:0!important;max-width:none!important";
      for (const element of [probe, ...probe.querySelectorAll<HTMLElement>("*")]) {
        element.style.setProperty("transition", "none", "important");
        element.style.setProperty("animation", "none", "important");
      }
      brand.append(probe);
      try {
        return probe.getBoundingClientRect().width;
      } finally {
        probe.remove();
      }
    };

    const update = () => {
      if (env.disposed()) return;
      cancelAnimationFrame(frameRequest);
      frameRequest = requestAnimationFrame(() => {
        if (options.expandWordmark === false) { publishExpansionAvailability(false); return; }
        if (options.fitWordmarkToHeader !== true && matchMedia("(min-width: 921px)").matches) {
          publishExpansionAvailability(true);
          stage.dataset.expansionMeasured = "true";
          stage.dataset.expansionRevision = String(
            Number(stage.dataset.expansionRevision || 0) + 1,
          );
          return;
        }
        const brandStyle = getComputedStyle(brand);
        const headerStyle = getComputedStyle(header);
        const eyeStyle = getComputedStyle(eye);
        const stageStyle = getComputedStyle(stage);
        const headerRect = header.getBoundingClientRect();
        const actionsRect = actions.getBoundingClientRect();
        const eyeRect = eye.getBoundingClientRect();
        const brandGap = Number.parseFloat(brandStyle.columnGap) || 0;
        const eyeMargins =
          (Number.parseFloat(eyeStyle.marginLeft) || 0) +
          (Number.parseFloat(eyeStyle.marginRight) || 0);
        const reservedStageWidth = (Number.parseFloat(stageStyle.fontSize) || 0) * 11.6;
        const expandedStageWidth = options.fitWordmarkToHeader === true
          ? Math.max(reservedStageWidth, measureExpandedWidth())
          : reservedStageWidth;
        const requiredWidth =
          eyeRect.width + eyeMargins + brandGap + expandedStageWidth;
        const availableWidth =
          headerRect.width -
          (Number.parseFloat(headerStyle.paddingLeft) || 0) -
          (Number.parseFloat(headerStyle.paddingRight) || 0) -
          (options.fitWordmarkToHeader === true ? Math.max(actionsRect.width, actions.scrollWidth) : actionsRect.width);
        publishExpansionAvailability(
          availableWidth + 0.5 >= requiredWidth,
        );
        stage.dataset.expansionMeasured = "true";
        stage.dataset.expansionRevision = String(
          Number(stage.dataset.expansionRevision || 0) + 1,
        );
      });
    };

    const resizeObserver = new ResizeObserver(update);
    resizeObserver.observe(header);
    resizeObserver.observe(actions);
    if (options.fitWordmarkToHeader === true) resizeObserver.observe(stage);
    on(window, "resize", update);
    update();
    document.fonts?.ready.then(update);
  }


  function mountBrandActivation(brand: HTMLElement, options: BrandOptions, env: Environment) {
    if (options.activation !== "reload-home") return;
    const { window, on, setTimeout } = env;
    const { location, history } = window;
    let scheduled = false;
    const activate = () => {
      if (scheduled) return;
      scheduled = true;
      const homeUrl = brand.getAttribute("href") ?? brand.dataset.homeUrl ?? "/";
      const currentPath = window.location.pathname.replace(/\/+$/, "") || "/";
      const home = new URL(homeUrl, location.href);
      const normalizedHome = home.pathname.replace(/\/+$/, "") || "/";
      if (home.origin !== location.origin || currentPath !== normalizedHome) {
        window.location.assign(homeUrl);
        return;
      }
      const hashlessUrl = `${window.location.pathname}${window.location.search}`;
      history.replaceState(history.state, "", hashlessUrl);
      window.dispatchEvent(new window.Event(BRAND_HOME_ACTIVATE_EVENT));
      setTimeout(() => location.reload(), BRAND_HOME_RELOAD_DELAY_MS);
    };
    on(brand, "click", (event: MouseEvent) => {
      if (event.button !== 0) return;
      event.preventDefault();
      activate();
    });
    on(brand, "keydown", (event: KeyboardEvent) => {
      if (event.repeat || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault();
      activate();
    });
  }


  function mountEye(brand: HTMLElement, options: BrandOptions, env: Environment) {
    const { document, window, on, requestAnimationFrame, ResizeObserver, matchMedia, performance, CSS, setInterval } = env;
    const eye = brand.querySelector<HTMLElement>(".tmc-eye");
    const eyeClip = eye?.querySelector<HTMLElement>(".tmc-eye__clip");
    const pupil = eye?.querySelector<HTMLElement>(".tmc-eye__pupil");
    if (!eye || !eyeClip || !pupil) return;

    const motionPreference = matchMedia("(prefers-reduced-motion: reduce)");
    let reduceMotion = motionPreference.matches;
    const blinkSlowdown = 2 / 3;
    const supportsPathClip = CSS.supports(
      "clip-path",
      'path("M 0 0 L 1 0 L 0 1 Z")',
    );
    let isHeldClosed = false;
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
    const lid = {
      openness: 0,
      from: 0,
      to: 1,
      startedAt: 0,
      duration: 0,
      phase: "idle" as "idle" | "closing" | "opening" | "closed-pause" | "held",
      kind: "auto" as "auto" | "manual",
      reopenAt: 0,
      width: 1,
      height: 1,
    };
    let animationFrame = 0;
    let scrollFrame = 0;
    let previousGazeFrameAt = 0;
    let lastPointer: { x: number; y: number } | null = null;

    const renderScrollTone = () => {
      scrollFrame = 0;
      if (options.reactiveGaze === true && lastPointer) aimAt(lastPointer.x, lastPointer.y);
      if (options.scrollTone === false) return;
      const scrollRange = Math.max(1, window.innerHeight);
      const whiteness = Math.max(0, Math.min(1, window.scrollY / scrollRange));
      const green = Math.round(210 + 45 * whiteness);
      const blue = Math.round(10 + 245 * whiteness);
      eye.style.setProperty("--eye-scroll-color", `rgb(255 ${green} ${blue})`);
      eye.style.setProperty(
        "--eye-scroll-outline-opacity",
        whiteness.toFixed(4),
      );
    };

    const renderLid = () => {
      const openness = Math.max(0, Math.min(1, lid.openness));
      if (openness > 0.9999) {
        eyeClip.style.clipPath = "none";
        eyeClip.style.setProperty("-webkit-clip-path", "none");
        return;
      }
      if (!supportsPathClip) {
        const inset = ((1 - openness) * 50).toFixed(3);
        const fallback = `inset(${inset}% 0 ${inset}% 0 round 50%)`;
        eyeClip.style.clipPath = fallback;
        eyeClip.style.setProperty("-webkit-clip-path", fallback);
        return;
      }
      const width = lid.width;
      const height = lid.height;
      const middle = height / 2;
      if (openness < 0.0001) {
        const closed = `path("M 0 ${middle.toFixed(3)} L ${width.toFixed(3)} ${middle.toFixed(3)} Z")`;
        eyeClip.style.clipPath = closed;
        eyeClip.style.setProperty("-webkit-clip-path", closed);
        return;
      }
      const halfChord = width / 2;
      const sagitta = middle * openness;
      const radius =
        (halfChord * halfChord + sagitta * sagitta) / (2 * sagitta);
      const path = `path("M 0 ${middle.toFixed(3)} A ${radius.toFixed(3)} ${radius.toFixed(3)} 0 0 1 ${width.toFixed(3)} ${middle.toFixed(3)} A ${radius.toFixed(3)} ${radius.toFixed(3)} 0 0 1 0 ${middle.toFixed(3)} Z")`;
      eyeClip.style.clipPath = path;
      eyeClip.style.setProperty("-webkit-clip-path", path);
    };

    const updateBounds = () => {
      const rect = eye.getBoundingClientRect();
      gaze.maxX = Math.max(1, rect.width * 0.255);
      gaze.maxY = Math.max(1, rect.height * 0.142);
      lid.width = rect.width;
      lid.height = rect.height;
      renderLid();
      if (options.reactiveGaze === true && lastPointer) aimAt(lastPointer.x, lastPointer.y);
    };
    const renderGaze = () => {
      const tilt = gaze.maxX ? (-gaze.x / gaze.maxX) * 2.8 : 0;
      pupil.style.setProperty("--gaze-x", `${gaze.x.toFixed(2)}px`);
      pupil.style.setProperty("--gaze-y", `${gaze.y.toFixed(2)}px`);
      pupil.style.setProperty("--gaze-tilt", `${tilt.toFixed(2)}deg`);
    };
    const constrain = () => {
      const ellipse =
        (gaze.x * gaze.x) / (gaze.maxX * gaze.maxX) +
        (gaze.y * gaze.y) / (gaze.maxY * gaze.maxY);
      if (ellipse > 1) {
        const scale = 1 / Math.sqrt(ellipse);
        gaze.x *= scale;
        gaze.y *= scale;
        gaze.vx *= 0.35;
        gaze.vy *= 0.35;
      }
    };
    const centerGaze = () => {
      gaze.targetX = 0;
      gaze.targetY = 0;
      if (reduceMotion && options.reactiveGaze === true) {
        gaze.x = gaze.y = gaze.vx = gaze.vy = 0;
        renderGaze();
      }
    };
    const resetPointer = () => {
      lastPointer = null;
      centerGaze();
    };
    const aimAt = (clientX: number, clientY: number) => {
      if (options.reactiveGaze === true) lastPointer = { x: clientX, y: clientY };
      const rect = eye.getBoundingClientRect();
      const dx = clientX - (rect.left + rect.width / 2);
      const dy = clientY - (rect.top + rect.height / 2);
      const distance = Math.hypot(dx, dy);
      if (!distance) {
        centerGaze();
        return;
      }
      const ux = dx / distance;
      const uy = dy / distance;
      const boundary =
        1 /
        Math.sqrt(
          (ux * ux) / (gaze.maxX * gaze.maxX) +
            (uy * uy) / (gaze.maxY * gaze.maxY),
        );
      const reach = Math.min(
        1,
        distance / (Math.min(window.innerWidth, window.innerHeight) * 0.34 + 40),
      );
      gaze.targetX = ux * boundary * reach;
      gaze.targetY = uy * boundary * reach;
      if (reduceMotion) {
        gaze.x = gaze.targetX;
        gaze.y = gaze.targetY;
        renderGaze();
      }
    };
    const smoothStep = (value: number) => value * value * (3 - 2 * value);
    const transitionLid = (target: number, baseDuration: number, kind: "auto" | "manual") => {
      const distance = Math.abs(target - lid.openness);
      lid.from = lid.openness;
      lid.to = target;
      lid.startedAt = performance.now();
      lid.duration = Math.max(
        (reduceMotion ? 70 : 90) * blinkSlowdown,
        baseDuration * blinkSlowdown * distance,
      );
      lid.phase = target === 0 ? "closing" : "opening";
      lid.kind = kind;
      lid.reopenAt = 0;
    };
    const blink = () => {
      if (isHeldClosed || document.hidden || lid.phase !== "idle") return;
      transitionLid(0, reduceMotion ? 120 : 220, "auto");
    };
    const blinkForTheme = () => {
      if (isHeldClosed || document.hidden) return;
      transitionLid(0, reduceMotion ? 120 : 220, "auto");
    };
    const holdClosed = () => {
      isHeldClosed = true;
      transitionLid(0, reduceMotion ? 120 : 240, "manual");
    };

    const updateLid = (now: number) => {
      if (lid.phase === "closed-pause") {
        if (isHeldClosed) lid.phase = "held";
        else if (now >= lid.reopenAt) {
          transitionLid(1, reduceMotion ? 150 : 280, lid.kind);
        }
        renderLid();
        return;
      }
      if (lid.phase === "held" || lid.phase === "idle") {
        renderLid();
        return;
      }
      const progress = Math.min(1, (now - lid.startedAt) / lid.duration);
      const eased = smoothStep(progress);
      lid.openness = lid.from + (lid.to - lid.from) * eased;
      renderLid();
      if (progress < 1) return;
      lid.openness = lid.to;
      if (lid.to === 0) {
        if (isHeldClosed) lid.phase = "held";
        else {
          lid.phase = "closed-pause";
          lid.reopenAt = now + (lid.kind === "auto" ? 55 : 35);
        }
      } else {
        lid.phase = "idle";
      }
    };
    const advanceGaze = (elapsedFrames: number) => {
      let remainingFrames = Math.min(4, Math.max(0, elapsedFrames));
      while (remainingFrames > 0.0001) {
        const frameStep = Math.min(1, remainingFrames);
        const damping = 0.79 ** frameStep;
        gaze.vx =
          (gaze.vx + (gaze.targetX - gaze.x) * 0.068 * frameStep) *
          damping;
        gaze.vy =
          (gaze.vy + (gaze.targetY - gaze.y) * 0.068 * frameStep) *
          damping;
        gaze.x += gaze.vx * frameStep;
        gaze.y += gaze.vy * frameStep;
        constrain();
        remainingFrames -= frameStep;
      }
    };
    const animate = (now: number) => {
      if (!reduceMotion) {
        const elapsedFrames = previousGazeFrameAt
          ? (now - previousGazeFrameAt) / (1000 / 60)
          : 1;
        previousGazeFrameAt = now;
        advanceGaze(elapsedFrames);
        renderGaze();
      }
      updateLid(now);
      animationFrame = requestAnimationFrame(animate);
    };

    renderScrollTone();
    updateBounds();
    transitionLid(1, reduceMotion ? 150 : 280, "auto");
    const boundsObserver = new ResizeObserver(updateBounds);
    boundsObserver.observe(eye);
    if (options.reactiveGaze === true) {
      boundsObserver.observe(brand);
      const header = brand.closest(".tmc-header");
      if (header) boundsObserver.observe(header);
    }
    on(window, 
      "scroll",
      () => {
        if (!scrollFrame) scrollFrame = requestAnimationFrame(renderScrollTone);
      },
      { passive: true },
    );
    on(window, 
      "resize",
      () => {
        updateBounds();
        renderScrollTone();
      },
      { passive: true },
    );
    on(window, "pointermove", (event: PointerEvent) => aimAt(event.clientX, event.clientY), {
      passive: true,
    });
    on(window, "pointerdown", (event: PointerEvent) => aimAt(event.clientX, event.clientY), {
      passive: true,
    });
    if (options.reactiveGaze === true) {
      on(motionPreference, "change", () => {
        reduceMotion = motionPreference.matches;
        gaze.vx = gaze.vy = 0;
        previousGazeFrameAt = 0;
        if (reduceMotion) {
          gaze.x = gaze.targetX;
          gaze.y = gaze.targetY;
          renderGaze();
        }
      });
    }
    on(window, BRAND_POINTER_EVENT, (event: CustomEvent<{ clientX?: number; clientY?: number }>) => {
      const clientX = Number(event.detail?.clientX);
      const clientY = Number(event.detail?.clientY);
      if (Number.isFinite(clientX) && Number.isFinite(clientY)) {
        aimAt(clientX, clientY);
      }
    });
    on(window, "blur", resetPointer);
    on(window, THEME_TOGGLE_BLINK_EVENT, blinkForTheme);
    on(window, BRAND_HOME_ACTIVATE_EVENT, holdClosed);
    on(document, "pointerout", (event: PointerEvent) => {
      if (!event.relatedTarget) resetPointer();
    });
    if (options.blinkIntervalMs !== 0) setInterval(blink, options.blinkIntervalMs ?? 30000);
    animationFrame = requestAnimationFrame(animate);
  }
