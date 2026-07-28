"use client";

import {
  useEffect,
  useLayoutEffect,
  useRef,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";

type Theme = "light" | "dark";
const THEME_TOGGLE_BLINK_EVENT = "tmc-theme-toggle-blink";
const BRAND_HOME_ACTIVATE_EVENT = "tmc-brand-home-activate";
const BRAND_HOME_RELOAD_DELAY_MS = 260;
const TENTACLE_POINTER_RELAY_EVENT = "tmc-tentacle-pointer-move";

function CurrentYear() {
  return <>{new Date().getFullYear()}</>;
}

function Ticker() {
  const tickerRef = useRef<HTMLDivElement>(null);
  const trackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ticker = tickerRef.current;
    const track = trackRef.current;
    if (!ticker || !track) return;

    let cancelled = false;
    let frameRequest = 0;
    let introAnimation: Animation | null = null;
    let loopAnimation: Animation | null = null;

    const start = () => {
      if (cancelled) return;

      frameRequest = window.requestAnimationFrame(() => {
        if (cancelled) return;

        const trackWidth = track.scrollWidth;
        const tickerWidth = ticker.clientWidth;
        const originalPixelsPerSecond = trackWidth / 56;
        const fullTravelSeconds = (trackWidth + tickerWidth) / originalPixelsPerSecond;
        const introTravelSeconds = fullTravelSeconds / 2;
        const pauseSeconds = 20;
        const loopSeconds = fullTravelSeconds + pauseSeconds;
        const travelEndOffset = fullTravelSeconds / loopSeconds;

        introAnimation = track.animate(
          [
            { transform: "translate3d(calc(50vw - 50%), 0, 0)" },
            { transform: "translate3d(-100%, 0, 0)" },
          ],
          {
            duration: introTravelSeconds * 1000,
            easing: "linear",
            fill: "forwards",
          },
        );

        loopAnimation = track.animate(
          [
            { transform: "translate3d(100vw, 0, 0)", offset: 0 },
            { transform: "translate3d(-100%, 0, 0)", offset: travelEndOffset },
            { transform: "translate3d(-100%, 0, 0)", offset: 1 },
          ],
          {
            delay: (introTravelSeconds + pauseSeconds) * 1000,
            duration: loopSeconds * 1000,
            easing: "linear",
            iterations: Infinity,
          },
        );
      });
    };

    if (document.fonts) {
      document.fonts.ready.then(start);
    } else {
      start();
    }

    return () => {
      cancelled = true;
      if (frameRequest) window.cancelAnimationFrame(frameRequest);
      introAnimation?.cancel();
      loopAnimation?.cancel();
    };
  }, []);

  return (
    <div className="ticker" aria-hidden="true" ref={tickerRef}>
      <div className="ticker__track" ref={trackRef}>
        VERIFY EVERYTHING <span>✦</span> EXPLAIN THE OUTPUT <span>✦</span> TEST AT AGENT SPEED <span>✦</span> DISTRIBUTE THE WORK <span>✦</span> VERIFY EVERYTHING <span>✦</span> EXPLAIN THE OUTPUT <span>✦</span>
      </div>
    </div>
  );
}

function InitialAnchorScroll() {
  useLayoutEffect(() => {
    let cancelled = false;
    let firstFrameRequest = 0;
    let secondFrameRequest = 0;

    const scrollToCurrentHash = () => {
      if (cancelled || !window.location.hash) return;

      const encodedId = window.location.hash.slice(1);
      let id = encodedId;

      try {
        id = decodeURIComponent(encodedId);
      } catch {
        // Keep the raw fragment when it is not valid URI-encoded text.
      }

      const target = document.getElementById(id);
      if (!target) return;

      const root = document.documentElement;
      const previousScrollBehavior = root.style.scrollBehavior;
      root.style.scrollBehavior = "auto";
      target.scrollIntoView({ block: "start" });
      root.style.scrollBehavior = previousScrollBehavior;
    };

    const alignCurrentHash = () => {
      if (cancelled) return;

      scrollToCurrentHash();
      if (firstFrameRequest) {
        window.cancelAnimationFrame(firstFrameRequest);
      }
      if (secondFrameRequest) {
        window.cancelAnimationFrame(secondFrameRequest);
      }

      firstFrameRequest = window.requestAnimationFrame(() => {
        scrollToCurrentHash();
        secondFrameRequest =
          window.requestAnimationFrame(scrollToCurrentHash);
      });
    };

    const pendingImages = Array.from(document.images).filter(
      (image) => !image.complete,
    );

    for (const image of pendingImages) {
      image.addEventListener("load", alignCurrentHash, { once: true });
      image.addEventListener("error", alignCurrentHash, { once: true });
    }

    alignCurrentHash();
    window.addEventListener("load", alignCurrentHash);
    window.addEventListener("pageshow", alignCurrentHash);
    window.addEventListener("hashchange", alignCurrentHash);
    document.fonts?.ready.then(alignCurrentHash);

    return () => {
      cancelled = true;
      if (firstFrameRequest) {
        window.cancelAnimationFrame(firstFrameRequest);
      }
      if (secondFrameRequest) {
        window.cancelAnimationFrame(secondFrameRequest);
      }
      for (const image of pendingImages) {
        image.removeEventListener("load", alignCurrentHash);
        image.removeEventListener("error", alignCurrentHash);
      }
      window.removeEventListener("load", alignCurrentHash);
      window.removeEventListener("pageshow", alignCurrentHash);
      window.removeEventListener("hashchange", alignCurrentHash);
    };
  }, []);

  return null;
}

function AnimatedEyeLogo() {
  const eyeRef = useRef<HTMLSpanElement>(null);
  const eyeClipRef = useRef<HTMLSpanElement>(null);
  const pupilRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const eye = eyeRef.current;
    const eyeClip = eyeClipRef.current;
    const pupil = pupilRef.current;
    if (!eye || !eyeClip || !pupil) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
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
      phase: "idle",
      kind: "auto",
      reopenAt: 0,
      width: 1,
      height: 1,
    };

    let animationFrame = 0;
    let blinkInterval = 0;
    let scrollFrame = 0;
    let previousGazeFrameAt = 0;

    const renderScrollTone = () => {
      scrollFrame = 0;
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

    const handleScroll = () => {
      if (!scrollFrame) {
        scrollFrame = window.requestAnimationFrame(renderScrollTone);
      }
    };

    const renderLid = () => {
      const openness = Math.max(0, Math.min(1, lid.openness));

      if (openness > 0.9999) {
        eyeClip.style.clipPath = "none";
        eyeClip.style.webkitClipPath = "none";
        return;
      }

      if (!supportsPathClip) {
        const inset = ((1 - openness) * 50).toFixed(3);
        const fallback = `inset(${inset}% 0 ${inset}% 0 round 50%)`;
        eyeClip.style.clipPath = fallback;
        eyeClip.style.webkitClipPath = fallback;
        return;
      }

      const width = lid.width;
      const height = lid.height;
      const middle = height / 2;

      if (openness < 0.0001) {
        const closedPath = `path("M 0 ${middle.toFixed(3)} L ${width.toFixed(3)} ${middle.toFixed(3)} Z")`;
        eyeClip.style.clipPath = closedPath;
        eyeClip.style.webkitClipPath = closedPath;
        return;
      }

      const halfChord = width / 2;
      const sagitta = middle * openness;
      const radius = (halfChord * halfChord + sagitta * sagitta) / (2 * sagitta);
      const path = `path("M 0 ${middle.toFixed(3)} A ${radius.toFixed(3)} ${radius.toFixed(3)} 0 0 1 ${width.toFixed(3)} ${middle.toFixed(3)} A ${radius.toFixed(3)} ${radius.toFixed(3)} 0 0 1 0 ${middle.toFixed(3)} Z")`;
      eyeClip.style.clipPath = path;
      eyeClip.style.webkitClipPath = path;
    };

    const updateBounds = () => {
      const rect = eye.getBoundingClientRect();
      gaze.maxX = Math.max(1, rect.width * 0.255);
      gaze.maxY = Math.max(1, rect.height * 0.142);
      lid.width = rect.width;
      lid.height = rect.height;
      renderLid();
    };

    const render = () => {
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

    const aimAt = (clientX: number, clientY: number) => {
      const rect = eye.getBoundingClientRect();
      const dx = clientX - (rect.left + rect.width / 2);
      const dy = clientY - (rect.top + rect.height / 2);
      const distance = Math.hypot(dx, dy);

      if (!distance) {
        gaze.targetX = 0;
        gaze.targetY = 0;
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
        render();
      }
    };

    const handlePointerMove = (event: PointerEvent) => aimAt(event.clientX, event.clientY);
    const handleRelayedPointerMove = (event: Event) => {
      const detail = (
        event as CustomEvent<{ clientX?: unknown; clientY?: unknown }>
      ).detail;
      const clientX = Number(detail?.clientX);
      const clientY = Number(detail?.clientY);
      if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return;
      aimAt(clientX, clientY);
    };
    const centerGaze = () => {
      gaze.targetX = 0;
      gaze.targetY = 0;
    };

    const handlePointerOut = (event: PointerEvent) => {
      if (!event.relatedTarget) centerGaze();
    };

    const smoothStep = (value: number) => value * value * (3 - 2 * value);

    const transitionLid = (
      target: number,
      baseDuration: number,
      kind: "auto" | "manual",
    ) => {
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

    const blinkForThemeToggle = () => {
      if (isHeldClosed || document.hidden) return;
      transitionLid(0, reduceMotion ? 120 : 220, "auto");
    };

    const holdClosedForHomeReload = () => {
      isHeldClosed = true;
      transitionLid(0, reduceMotion ? 120 : 240, "manual");
    };

    const updateLid = (now: number) => {
      if (lid.phase === "closed-pause") {
        if (isHeldClosed) {
          lid.phase = "held";
        } else if (now >= lid.reopenAt) {
          transitionLid(1, reduceMotion ? 150 : 280, lid.kind as "auto" | "manual");
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
        if (isHeldClosed) {
          lid.phase = "held";
        } else {
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
        render();
      }

      updateLid(now);
      animationFrame = window.requestAnimationFrame(animate);
    };

    const handleWindowBlur = () => {
      centerGaze();
    };
    const handleViewportResize = () => {
      updateBounds();
      renderScrollTone();
    };

    renderScrollTone();
    updateBounds();
    transitionLid(1, reduceMotion ? 150 : 280, "auto");
    const resizeObserver = new ResizeObserver(updateBounds);
    resizeObserver.observe(eye);
    window.addEventListener("scroll", handleScroll, { passive: true });
    window.addEventListener("resize", handleViewportResize, { passive: true });
    window.addEventListener("pointermove", handlePointerMove, { passive: true });
    window.addEventListener("pointerdown", handlePointerMove, { passive: true });
    window.addEventListener(
      TENTACLE_POINTER_RELAY_EVENT,
      handleRelayedPointerMove,
    );
    window.addEventListener("blur", handleWindowBlur);
    window.addEventListener(THEME_TOGGLE_BLINK_EVENT, blinkForThemeToggle);
    window.addEventListener(
      BRAND_HOME_ACTIVATE_EVENT,
      holdClosedForHomeReload,
    );
    document.addEventListener("pointerout", handlePointerOut);

    blinkInterval = window.setInterval(blink, 30000);
    animationFrame = window.requestAnimationFrame(animate);

    return () => {
      resizeObserver.disconnect();
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      if (scrollFrame) window.cancelAnimationFrame(scrollFrame);
      if (blinkInterval) window.clearInterval(blinkInterval);
      window.removeEventListener("scroll", handleScroll);
      window.removeEventListener("resize", handleViewportResize);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerdown", handlePointerMove);
      window.removeEventListener(
        TENTACLE_POINTER_RELAY_EVENT,
        handleRelayedPointerMove,
      );
      window.removeEventListener("blur", handleWindowBlur);
      window.removeEventListener(THEME_TOGGLE_BLINK_EVENT, blinkForThemeToggle);
      window.removeEventListener(
        BRAND_HOME_ACTIVATE_EVENT,
        holdClosedForHomeReload,
      );
      document.removeEventListener("pointerout", handlePointerOut);
    };
  }, []);

  return (
    <span
      className="animated-eye-logo"
      ref={eyeRef}
      aria-hidden="true"
    >
      <span className="animated-eye-logo__clip" ref={eyeClipRef}>
        <span className="animated-eye-logo__outline" aria-hidden="true" />
        <span className="animated-eye-logo__surface" aria-hidden="true" />
        <span className="animated-eye-logo__pupil" ref={pupilRef} aria-hidden="true" />
      </span>
    </span>
  );
}

const pillars = [
  {
    number: "01",
    title: "Verifiability",
    copy: "Proof-oriented infrastructure that turns ‘looks good’ into evidence an agent can act on.",
  },
  {
    number: "02",
    title: "Interpretability",
    copy: "Human-scale explanations for machine-scale output, so teams can understand what changed and why.",
  },
  {
    number: "03",
    title: "Testing at agent speed",
    copy: "Fast, deterministic checks built for workers that never get tired and rarely stop at one attempt.",
  },
];

function ThemeToggle() {
  useEffect(() => {
    const darkMedia = window.matchMedia("(prefers-color-scheme: dark)");
    const lightMedia = window.matchMedia("(prefers-color-scheme: light)");

    const syncSystemTheme = () => {
      let storedTheme: string | null = null;

      try {
        storedTheme = window.localStorage.getItem("tmc-theme");
      } catch {
        // Keep following the system theme when browser storage is unavailable.
      }

      if (storedTheme === "dark" || storedTheme === "light") return;

      document.documentElement.dataset.theme = darkMedia.matches
        ? "dark"
        : lightMedia.matches
          ? "light"
          : "dark";
    };

    darkMedia.addEventListener("change", syncSystemTheme);
    lightMedia.addEventListener("change", syncSystemTheme);
    document.documentElement.dataset.themeReady = "true";

    return () => {
      darkMedia.removeEventListener("change", syncSystemTheme);
      lightMedia.removeEventListener("change", syncSystemTheme);
    };
  }, []);

  const toggleTheme = () => {
    const currentTheme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
    const nextTheme: Theme = currentTheme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem("tmc-theme", nextTheme);
    window.dispatchEvent(new Event(THEME_TOGGLE_BLINK_EVENT));
  };

  return (
    <button
      className="theme-toggle"
      type="button"
      onClick={toggleTheme}
      aria-label="Toggle color theme"
    >
      <span className="theme-toggle__track" aria-hidden="true">
        <span className="theme-toggle__sun">
          <span className="theme-toggle__sun-core" />
          <span className="theme-toggle__sun-rays" />
          <span className="theme-toggle__sun-rays theme-toggle__sun-rays--diagonal" />
        </span>
        <span className="theme-toggle__moon" />
        <span className="theme-toggle__thumb" />
      </span>
    </button>
  );
}

function AnimatedWordmark() {
  return (
    <span className="brand__wordmark-stage" aria-hidden="true">
      <span className="brand__wordmark">
        <span className="brand__word-part brand__word-part--t">
          <span className="brand__word-anchor">T</span>
          <span className="brand__word-reveal" data-text="he">
            he
          </span>
        </span>
        <span className="brand__word-part brand__word-part--m">
          <span className="brand__word-anchor">M</span>
          <span className="brand__word-reveal" data-text="utating">
            utating
          </span>
        </span>
        <span className="brand__word-part">
          <span className="brand__word-anchor">C</span>
          <span className="brand__word-reveal" data-text="ompany">
            ompany
          </span>
        </span>
      </span>
    </span>
  );
}

function SourcesLinkLabel() {
  return (
    <>
      <span>Sources</span>
      <svg
        className="source-link__icon"
        viewBox="0 0 16 16"
        aria-hidden="true"
      >
        <path d="M6.766 11.328c-2.063-.25-3.516-1.734-3.516-3.656 0-.781.281-1.625.75-2.188-.203-.515-.172-1.609.063-2.062.625-.078 1.468.25 1.968.703.594-.187 1.219-.281 1.985-.281.765 0 1.39.094 1.953.265.484-.437 1.344-.765 1.969-.687.218.422.25 1.515.046 2.047.5.593.766 1.39.766 2.203 0 1.922-1.453 3.375-3.547 3.64.531.344.89 1.094.89 1.954v1.625c0 .468.391.734.86.547C13.781 14.359 16 11.53 16 8.03 16 3.61 12.406 0 7.984 0 3.563 0 0 3.61 0 8.031a7.88 7.88 0 0 0 5.172 7.422c.422.156.828-.125.828-.547v-1.25c-.219.094-.5.156-.75.156-1.031 0-1.64-.562-2.078-1.609-.172-.422-.36-.672-.719-.719-.187-.015-.25-.093-.25-.187 0-.188.313-.328.625-.328.453 0 .844.281 1.25.86.313.452.64.655 1.031.655s.641-.14 1-.5c.266-.265.47-.5.657-.656" />
      </svg>
    </>
  );
}

function BrandHomeControl() {
  const reloadScheduledRef = useRef(false);

  const activateHome = () => {
    if (reloadScheduledRef.current) return;
    reloadScheduledRef.current = true;

    const hashlessUrl = `${window.location.pathname}${window.location.search}`;
    window.history.replaceState(window.history.state, "", hashlessUrl);
    window.dispatchEvent(new Event(BRAND_HOME_ACTIVATE_EVENT));
    window.setTimeout(
      () => window.location.reload(),
      BRAND_HOME_RELOAD_DELAY_MS,
    );
  };

  const handleClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    activateHome();
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.repeat || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    activateHome();
  };

  return (
    <div
      className="brand"
      role="link"
      tabIndex={0}
      aria-label="Reload The Mutating Company home page"
      onClick={handleClick}
      onKeyDown={handleKeyDown}
    >
      <span className="brand__mark brand__mark--eye">
        <AnimatedEyeLogo />
      </span>
      <AnimatedWordmark />
    </div>
  );
}

function Header() {
  const mobileMenuRef = useRef<HTMLDetailsElement>(null);
  const closeMobileMenu = () => mobileMenuRef.current?.removeAttribute("open");

  return (
    <header className="site-header">
      <BrandHomeControl />

      <nav className="main-nav" aria-label="Main navigation">
        <a className="source-link" href="https://github.com/mutating">
          <SourcesLinkLabel />
        </a>
        <a href="#field-notes">Blog</a>
        <a href="#contact">Contact</a>
      </nav>

      <div className="header-actions">
        <span className="language" aria-label="Current language: English">
          EN <span aria-hidden="true">↘</span>
        </span>
        <ThemeToggle />
        <details className="mobile-menu" ref={mobileMenuRef}>
          <summary aria-label="Open navigation menu">Menu</summary>
          <nav aria-label="Mobile navigation">
            <a
              className="source-link"
              href="https://github.com/mutating"
              onClick={closeMobileMenu}
            >
              <SourcesLinkLabel />
            </a>
            <a href="#field-notes" onClick={closeMobileMenu}>Blog</a>
            <a href="#contact" onClick={closeMobileMenu}>Contact</a>
          </nav>
        </details>
      </div>
    </header>
  );
}

function ComplexityChart() {
  const chartRef = useRef<HTMLDivElement>(null);
  const staticCanvasRef = useRef<HTMLCanvasElement>(null);
  const curvesCanvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const chart = chartRef.current;
    const staticCanvas = staticCanvasRef.current;
    const curvesCanvas = curvesCanvasRef.current;
    if (!chart || !staticCanvas || !curvesCanvas) return;

    const draw = () => {
      const rect = chart.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = rect.width;
      const height = 330;
      const pad = { top: 30, right: 24, bottom: 48, left: 42 };
      const plotWidth = width - pad.left - pad.right;
      const plotHeight = height - pad.top - pad.bottom;
      const styles = getComputedStyle(document.documentElement);
      const ink = styles.getPropertyValue("--ink-muted").trim() || "#8d8790";
      const grid = styles.getPropertyValue("--line").trim() || "#ddd6dc";
      const hot = styles.getPropertyValue("--hot").trim() || "#ff3e9d";
      const hotSoft = styles.getPropertyValue("--hot-soft").trim() || "#b15b87";

      for (const canvas of [staticCanvas, curvesCanvas]) {
        canvas.width = Math.max(1, Math.floor(width * dpr));
        canvas.height = Math.floor(height * dpr);
      }

      const staticContext = staticCanvas.getContext("2d");
      const curvesContext = curvesCanvas.getContext("2d");
      if (!staticContext || !curvesContext) return;

      staticContext.scale(dpr, dpr);
      curvesContext.scale(dpr, dpr);
      staticContext.clearRect(0, 0, width, height);
      curvesContext.clearRect(0, 0, width, height);

      staticContext.lineWidth = 1;
      staticContext.strokeStyle = grid;
      staticContext.setLineDash([3, 6]);
      for (let i = 0; i <= 4; i += 1) {
        const y = pad.top + (plotHeight / 4) * i;
        staticContext.beginPath();
        staticContext.moveTo(pad.left, y);
        staticContext.lineTo(width - pad.right, y);
        staticContext.stroke();
      }
      staticContext.setLineDash([]);

      const plot = (fn: (x: number) => number, color: string, lineWidth: number) => {
        curvesContext.beginPath();
        for (let i = 0; i <= 160; i += 1) {
          const t = i / 160;
          const x = pad.left + t * plotWidth;
          const value = Math.min(1, fn(t));
          const y = pad.top + plotHeight - value * plotHeight;
          if (i === 0) curvesContext.moveTo(x, y);
          else curvesContext.lineTo(x, y);
        }
        curvesContext.lineWidth = lineWidth;
        curvesContext.strokeStyle = color;
        curvesContext.lineCap = "round";
        curvesContext.lineJoin = "round";
        curvesContext.stroke();
      };

      plot((x) => (Math.exp(4.35 * x) - 1) / (Math.exp(4.35) - 1), hot, 4);
      plot((x) => 0.06 + 0.28 * x + 0.16 * x * x, hotSoft, 3);

      staticContext.fillStyle = ink;
      staticContext.font = "500 11px var(--font-geist-mono), monospace";
      staticContext.textAlign = "left";
      staticContext.fillText("CHANGE COST", 0, 14);
      staticContext.textAlign = "right";
      staticContext.fillText("SYSTEM SCALE →", width - pad.right, height - 10);

      curvesContext.font = "600 11px var(--font-geist-mono), monospace";
      curvesContext.fillStyle = hot;
      curvesContext.textAlign = "right";
      curvesContext.fillText("UNCHECKED ENTROPY", width - pad.right - 28, pad.top + 15);
      curvesContext.fillStyle = hotSoft;
      curvesContext.fillText("WITH VERIFICATION", width - pad.right - 4, pad.top + plotHeight * 0.65);
    };

    let hasRevealed = false;
    let revealObserver: IntersectionObserver | null = null;
    let revealObserverGeneration = 0;
    const visibilityTarget = chart.closest(".chart-panel") ?? chart;

    const observeForReveal = () => {
      if (hasRevealed) return;

      revealObserver?.disconnect();
      revealObserverGeneration += 1;
      const observerGeneration = revealObserverGeneration;
      const targetHeight = visibilityTarget.getBoundingClientRect().height;
      const maximumVisibleRatio = Math.min(
        1,
        window.innerHeight / Math.max(1, targetHeight),
      );
      const revealThreshold = Math.min(
        0.985,
        Math.max(0.5, maximumVisibleRatio - 0.01),
      );

      revealObserver = new IntersectionObserver(
        (entries) => {
          if (
            hasRevealed ||
            observerGeneration !== revealObserverGeneration ||
            !entries.some(
              (entry) =>
                entry.isIntersecting &&
                entry.intersectionRatio >= revealThreshold,
            )
          ) {
            return;
          }

          hasRevealed = true;
          chart.dataset.curvesRevealed = "true";
          revealObserver?.disconnect();
          revealObserver = null;
        },
        { threshold: revealThreshold },
      );
      revealObserver.observe(visibilityTarget);
    };

    draw();
    observeForReveal();
    const resizeObserver = new ResizeObserver(() => {
      draw();
      observeForReveal();
    });
    resizeObserver.observe(chart);
    if (visibilityTarget !== chart) {
      resizeObserver.observe(visibilityTarget);
    }
    window.addEventListener("resize", observeForReveal);
    const themeObserver = new MutationObserver(draw);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    return () => {
      resizeObserver.disconnect();
      themeObserver.disconnect();
      window.removeEventListener("resize", observeForReveal);
      revealObserverGeneration += 1;
      revealObserver?.disconnect();
    };
  }, []);

  return (
    <div
      className="complexity-chart"
      ref={chartRef}
      role="img"
      aria-label="Unchecked software complexity rises exponentially with system scale, while deterministic verification keeps the curve much flatter."
      data-curves-revealed="false"
    >
      <canvas
        className="complexity-chart__layer complexity-chart__static"
        ref={staticCanvasRef}
        aria-hidden="true"
      />
      <canvas
        className="complexity-chart__layer complexity-chart__curves"
        ref={curvesCanvasRef}
        aria-hidden="true"
      />
    </div>
  );
}

function FeedbackLoop() {
  return (
    <div className="feedback-loop" role="img" aria-label="A feedback loop between product, tests, signals, and agents">
      <div className="feedback-node feedback-node--top">
        <span>01</span>
        Better product
      </div>
      <div className="feedback-arrow feedback-arrow--one" aria-hidden="true">
        ↘
      </div>
      <div className="feedback-node feedback-node--right">
        <span>02</span>
        Faster tests
      </div>
      <div className="feedback-arrow feedback-arrow--two" aria-hidden="true">
        ↙
      </div>
      <div className="feedback-node feedback-node--bottom">
        <span>03</span>
        Sharper signals
      </div>
      <div className="feedback-arrow feedback-arrow--three" aria-hidden="true">
        ↖
      </div>
      <div className="feedback-node feedback-node--left">
        <span>04</span>
        Smarter agents
      </div>
      <div className="feedback-arrow feedback-arrow--four" aria-hidden="true">
        ↗
      </div>
      <div className="feedback-core">LOOP<br />GAINS</div>
    </div>
  );
}

function NetworkVisual() {
  return (
    <div className="network-visual" role="img" aria-label="A local machine connected to a scalable distributed verification network">
      <div className="network-orbit network-orbit--outer" />
      <div className="network-orbit network-orbit--inner" />
      <div className="network-node network-node--a">A</div>
      <div className="network-node network-node--b">B</div>
      <div className="network-node network-node--c">C</div>
      <div className="network-node network-node--d">D</div>
      <div className="network-core">
        <span>LOCAL</span>
        1×
      </div>
      <p>One command surface.<br />As many workers as needed.</p>
    </div>
  );
}

function TokamakVisual() {
  const shellRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const shell = shellRef.current;
    const frame = frameRef.current;
    if (!shell || !frame) return;

    const edgeOnRotation = -Math.atan2(9.42, 16.5);
    let frameRequest = 0;
    let modelIsNearViewport = false;

    const updateRotation = () => {
      frameRequest = 0;

      const rect = shell.getBoundingClientRect();
      const visible =
        modelIsNearViewport &&
        !document.hidden &&
        rect.bottom >= -200 &&
        rect.top <= window.innerHeight + 200;
      const distanceFromEntryToCenter = (window.innerHeight + rect.height) / 2;
      const scrollProgress = Math.min(
        2,
        Math.max(0, (window.innerHeight - rect.top) / distanceFromEntryToCenter),
      );
      const rotationX = edgeOnRotation * scrollProgress;
      const responsiveHeight =
        window.innerWidth <= 580 ? 410 : window.innerWidth <= 860 ? 520 : 600;
      const modelScale =
        1 / (1 + (600 - responsiveHeight) * 0.00125);
      const theme =
        document.documentElement.dataset.theme === "dark" ? "dark" : "light";

      shell.dataset.scrollProgress = scrollProgress.toFixed(3);
      shell.dataset.rotationX = rotationX.toFixed(3);
      shell.dataset.modelScale = modelScale.toFixed(3);
      frame.contentWindow?.postMessage(
        { type: "tokamak-state", rotationX, modelScale, theme, visible },
        window.location.origin,
      );
    };

    const scheduleRotationUpdate = () => {
      if (!frameRequest) {
        frameRequest = window.requestAnimationFrame(updateRotation);
      }
    };

    const handleScroll = () => {
      if (frameRequest) {
        window.cancelAnimationFrame(frameRequest);
        frameRequest = 0;
      }
      updateRotation();
    };

    const handleModelMessage = (event: MessageEvent) => {
      if (event.source !== frame.contentWindow) return;

      if (event.data?.type === "tokamak-ready") {
        scheduleRotationUpdate();
      } else if (event.data?.type === "tokamak-state-applied") {
        shell.classList.add("is-ready");
      }
    };

    const resizeObserver = new ResizeObserver(scheduleRotationUpdate);
    const themeObserver = new MutationObserver(scheduleRotationUpdate);
    const visibilityObserver = new IntersectionObserver(
      ([entry]) => {
        modelIsNearViewport = entry.isIntersecting;
        scheduleRotationUpdate();
      },
      { rootMargin: "200px 0px" },
    );
    resizeObserver.observe(shell);
    visibilityObserver.observe(shell);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    frame.addEventListener("load", scheduleRotationUpdate);
    document.addEventListener("visibilitychange", scheduleRotationUpdate);
    window.addEventListener("message", handleModelMessage);
    window.addEventListener("scroll", handleScroll, { passive: true });
    window.addEventListener("resize", scheduleRotationUpdate);
    scheduleRotationUpdate();

    return () => {
      if (frameRequest) window.cancelAnimationFrame(frameRequest);
      resizeObserver.disconnect();
      visibilityObserver.disconnect();
      themeObserver.disconnect();
      frame.removeEventListener("load", scheduleRotationUpdate);
      document.removeEventListener(
        "visibilitychange",
        scheduleRotationUpdate,
      );
      window.removeEventListener("message", handleModelMessage);
      window.removeEventListener("scroll", handleScroll);
      window.removeEventListener("resize", scheduleRotationUpdate);
    };
  }, []);

  return (
    <div className="tokamak-model" ref={shellRef}>
      <iframe
        ref={frameRef}
        className="tokamak-model__frame"
        src="/tokamak-reactor.html?v=performance-6"
        title="Scroll-linked three-dimensional tokamak reactor"
        tabIndex={-1}
        aria-label="A three-dimensional tokamak reactor whose angle follows its position in the viewport"
      />
    </div>
  );
}

function TentacleContactBackground() {
  const frameRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    let contactIsNearViewport = false;
    let scrollIsActive = false;
    let scrollIdleTimer = 0;
    const scrollIdleFallbackMs = 320;

    const sendTheme = () => {
      const theme: Theme =
        document.documentElement.dataset.theme === "dark" ? "dark" : "light";
      frame.contentWindow?.postMessage(
        { type: "muta-tentacles-theme", theme },
        window.location.origin,
      );
    };

    const sendRuntimeState = () => {
      const state = {
        visible: contactIsNearViewport && !document.hidden,
        scrollActive: scrollIsActive,
      };
      frame.dataset.scrollActive = String(scrollIsActive);
      const runtimeWindow = frame.contentWindow as
        | (Window & {
            __mutaTentaclesSetRuntimeState?: (
              nextState: typeof state,
            ) => void;
          })
        | null;
      try {
        if (runtimeWindow?.__mutaTentaclesSetRuntimeState) {
          runtimeWindow.__mutaTentaclesSetRuntimeState(state);
          return;
        }
      } catch {
        // Fall through to postMessage if the frame is not same-origin.
      }
      runtimeWindow?.postMessage(
        { type: "muta-tentacles-runtime", ...state },
        window.location.origin,
      );
    };

    const releaseTentacles = () => {
      frame.contentWindow?.postMessage(
        { type: "muta-tentacles-release" },
        window.location.origin,
      );
    };

    const handleVisibilityChange = () => {
      if (document.hidden) {
        releaseTentacles();
      }
      sendRuntimeState();
    };

    const clearScrollIdleTimer = () => {
      if (!scrollIdleTimer) return;
      window.clearTimeout(scrollIdleTimer);
      scrollIdleTimer = 0;
    };

    const finishScroll = () => {
      clearScrollIdleTimer();
      if (!scrollIsActive) return;
      scrollIsActive = false;
      sendRuntimeState();
    };

    const cancelBoundaryScroll = () => {
      clearScrollIdleTimer();
      scrollIsActive = false;
      // The child can enter suppression before this cross-frame wheel intent
      // arrives, so always synchronize the final boundary state.
      sendRuntimeState();
    };

    const canPageScrollInDirection = (deltaY: number) => {
      if (!Number.isFinite(deltaY) || deltaY === 0) return false;
      const root =
        document.scrollingElement ?? document.documentElement;
      const maximumScrollTop = Math.max(
        0,
        root.scrollHeight - root.clientHeight,
      );
      const boundaryTolerance = 1;
      if (deltaY > 0) {
        return root.scrollTop < maximumScrollTop - boundaryTolerance;
      }
      return root.scrollTop > boundaryTolerance;
    };

    const handleScroll = () => {
      if (!contactIsNearViewport) return;
      if (!scrollIsActive) {
        scrollIsActive = true;
        sendRuntimeState();
      }
      if (scrollIdleTimer) {
        window.clearTimeout(scrollIdleTimer);
      }
      scrollIdleTimer = window.setTimeout(
        finishScroll,
        scrollIdleFallbackMs,
      );
    };

    const handleForwardedScrollInput = (deltaY: unknown) => {
      const numericDeltaY = Number(deltaY);
      if (!Number.isFinite(numericDeltaY) || numericDeltaY === 0) {
        return;
      }
      if (!canPageScrollInDirection(numericDeltaY)) {
        cancelBoundaryScroll();
        return;
      }
      handleScroll();
    };

    const handleMessage = (event: MessageEvent) => {
      if (
        event.source === frame.contentWindow &&
        event.data?.type === "muta-tentacles-ready"
      ) {
        sendTheme();
        sendRuntimeState();
      } else if (
        event.source === frame.contentWindow &&
        event.data?.type === "muta-tentacles-scroll-input"
      ) {
        handleForwardedScrollInput(event.data.deltaY);
      } else if (
        event.source === frame.contentWindow &&
        event.data?.type === "muta-tentacles-pointer-move"
      ) {
        const normalizedX = Number(event.data.x);
        const normalizedY = Number(event.data.y);
        if (
          !Number.isFinite(normalizedX) ||
          !Number.isFinite(normalizedY)
        ) {
          return;
        }
        const rect = frame.getBoundingClientRect();
        window.dispatchEvent(
          new CustomEvent(TENTACLE_POINTER_RELAY_EVENT, {
            detail: {
              clientX:
                rect.left +
                Math.max(0, Math.min(1, normalizedX)) * rect.width,
              clientY:
                rect.top +
                Math.max(0, Math.min(1, normalizedY)) * rect.height,
            },
          }),
        );
      }
    };

    const themeObserver = new MutationObserver(sendTheme);
    const visibilityObserver = new IntersectionObserver(
      ([entry]) => {
        contactIsNearViewport = entry.isIntersecting;
        if (!contactIsNearViewport) {
          clearScrollIdleTimer();
          scrollIsActive = false;
        }
        sendRuntimeState();
      },
      { rootMargin: "200px 0px" },
    );
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    visibilityObserver.observe(frame);
    frame.addEventListener("load", sendTheme);
    window.addEventListener("message", handleMessage);
    window.addEventListener("scroll", handleScroll, { passive: true });
    window.addEventListener("scrollend", finishScroll, { passive: true });
    window.addEventListener("pointerup", releaseTentacles, { passive: true });
    window.addEventListener("pointercancel", releaseTentacles, {
      passive: true,
    });
    window.addEventListener("blur", releaseTentacles);
    window.addEventListener("pagehide", releaseTentacles);
    document.addEventListener(
      "visibilitychange",
      handleVisibilityChange,
    );
    sendTheme();
    sendRuntimeState();

    return () => {
      clearScrollIdleTimer();
      themeObserver.disconnect();
      visibilityObserver.disconnect();
      frame.removeEventListener("load", sendTheme);
      window.removeEventListener("message", handleMessage);
      window.removeEventListener("scroll", handleScroll);
      window.removeEventListener("scrollend", finishScroll);
      window.removeEventListener("pointerup", releaseTentacles);
      window.removeEventListener("pointercancel", releaseTentacles);
      window.removeEventListener("blur", releaseTentacles);
      window.removeEventListener("pagehide", releaseTentacles);
      document.removeEventListener(
        "visibilitychange",
        handleVisibilityChange,
      );
    };
  }, []);

  return (
    <iframe
      ref={frameRef}
      className="contact__tentacles"
      src="/muta-tentacles.html?v=e8a6f8306498&embedded=1"
      title="Interactive field of cursor-responsive Muta tentacles"
      loading="eager"
      tabIndex={-1}
    />
  );
}

export default function Home() {
  return (
    <main>
      <InitialAnchorScroll />
      <Header />

      <section className="hero section-shell">
        <div className="hero__copy">
          <p className="eyebrow">
            Infrastructure for software-building agents <span>— <CurrentYear /></span>
          </p>
          <h1>
            AI systems
            <br />
            that <em>doubt.</em>
          </h1>
          <p className="hero__lede">
            We build deterministic verification, interpretability, and testing infrastructure for the agents writing tomorrow’s software.
          </p>
          <div className="hero__actions">
            <a className="button button--primary" href="#sections">
              Read our thesis <span aria-hidden="true">↓</span>
            </a>
            <a className="text-link" href="#open-source">
              Explore the open-source lab <span aria-hidden="true">↗</span>
            </a>
          </div>
          <div className="hero__signal" aria-label="System status">
            <span className="signal-dot" />
            <span>Verifier online</span>
            <span>Confidence: earned, not assumed</span>
          </div>
        </div>

        <figure className="mascot-card">
          <div className="mascot-card__halo" />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            className="mascot-card__art"
            src="/muta-v2-transparent.png"
            srcSet="/muta-v2-transparent.png 1x, /muta-v2-transparent-2x-v9.webp 2x"
            alt="A cheerful pink one-eyed mutant with a suspicious grin"
          />
          <figcaption>
            <span>Meet Muta</span>
            Chief Doubt Officer
          </figcaption>
        </figure>
      </section>

      <Ticker />

      <span className="section-list-anchor" id="sections" aria-hidden="true" />

      <section className="complexity section-shell" id="complexity" aria-labelledby="complexity-heading">
        <div className="complexity__copy">
          <a className="section-index" href="#complexity">01 / COMPLEXITY</a>
          <h2 id="complexity-heading">Entropy whispers. Then it sends an invoice.</h2>
          <p>
            Software complexity compounds nonlinearly and hides well in the early days. Left alone, it makes every future change slower, riskier, and more expensive.
          </p>
          <p>
            Advanced testing and verification bend that curve. The bigger the system, the more dramatic the payoff.
          </p>
        </div>
        <div className="chart-panel">
          <div className="chart-panel__header">
            <span>COMPLEXITY / CHANGE COST</span>
            <span>MODEL 04.35</span>
          </div>
          <ComplexityChart />
          <p className="chart-panel__caption">Illustrative model—not a promise from marketing.</p>
        </div>
      </section>

      <section className="containment section-shell" id="containment" aria-labelledby="containment-heading">
        <div className="containment__visual">
          <TokamakVisual />
        </div>
        <div className="containment__copy">
          <a className="section-index" href="#containment">02 / CONTAINMENT</a>
          <h2 id="containment-heading">Hold the plasma. Aim the power.</h2>
          <p className="large-copy">
            Agents are innately nondeterministic. That is part of their power—and exactly why they need a field around them.
          </p>
          <p>
            Deterministic verification stabilizes that energy like magnetic confinement in a{" "}
            <a
              className="inline-text-link"
              href="https://en.wikipedia.org/wiki/Tokamak"
              target="_blank"
              rel="noreferrer"
            >
              tokamak
            </a>
            : containing it, shaping it, and turning raw force into useful work without pretending the plasma has become predictable.
          </p>
          <div className="containment__formula containment__formula--desktop">
            <span>agent variance</span>
            <b>×</b>
            <span>deterministic checks</span>
            <b>=</b>
            <span>directed capability</span>
          </div>
        </div>
        <div className="containment__formula containment__formula--mobile">
          <span>agent variance</span>
          <b>×</b>
          <span>deterministic checks</span>
          <b>=</b>
          <span>directed capability</span>
        </div>
      </section>

      <section className="manifesto section-shell" id="thesis">
        <a className="section-index" href="#thesis">03 / OUR THESIS</a>
        <div className="manifesto__headline">
          <h2>More intelligence deserves more resistance.</h2>
        </div>
        <div className="manifesto__body">
          <p className="large-copy">
            Agents can ship more work than human-era systems were designed to inspect. Old feedback loops become bottlenecks. Quiet mistakes become architecture.
          </p>
          <p>
            Our answer is not to slow agents down. It is to pair them with rigorous systems that question every result, point to concrete problems, and demand another pass when the evidence is weak.
          </p>
          <p>
            Techniques once dismissed as too expensive become ordinary when labor is no longer the constraint. That changes the economics of correctness—and the possible scale of software itself.
          </p>
        </div>
      </section>

      <section className="pillars section-shell" id="what-we-build" aria-labelledby="pillars-heading">
        <div className="pillars__intro">
          <a className="section-index" href="#what-we-build">04 / WHAT WE BUILD</a>
          <h2 id="pillars-heading">A skeptical layer for the agentic stack.</h2>
        </div>
        <div className="pillars__grid">
          {pillars.map((pillar) => (
            <article className="pillar-card" key={pillar.number}>
              <span className="pillar-card__number">{pillar.number}</span>
              <div className="pillar-card__icon" aria-hidden="true">
                {pillar.number === "01" ? "✓?" : pillar.number === "02" ? "≋" : "↯"}
              </div>
              <h3>{pillar.title}</h3>
              <p>{pillar.copy}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="distribution section-shell" id="scale" aria-labelledby="distribution-heading">
        <div className="distribution__copy">
          <a className="section-index" href="#scale">05 / SCALE</a>
          <h2 id="distribution-heading">Local when you need it. Distributed when you mean it.</h2>
          <p>
            Verification throughput should never be capped by one machine. Our systems are designed to spread work across many workers without giving up the ability to run anywhere—even on a laptop, offline, under your desk.
          </p>
          <ul>
            <li><span>01</span> Same checks, any topology</li>
            <li><span>02</span> Deterministic results across workers</li>
            <li><span>03</span> Scale without changing the mental model</li>
          </ul>
        </div>
        <NetworkVisual />
      </section>

      <section className="feedback section-shell" id="feedback" aria-labelledby="feedback-heading">
        <div className="feedback__visual">
          <FeedbackLoop />
        </div>
        <div className="feedback__copy">
          <a className="section-index" href="#feedback">06 / FEEDBACK</a>
          <h2 id="feedback-heading">Good systems make each other better.</h2>
          <p className="large-copy">
            Great tests do more than catch bugs. They expose unstable surfaces and guide agents toward stronger designs.
          </p>
          <p>
            Better products make tests faster and more precise. Better tests create better signals. Better signals train better decisions. The loop gains power when every part is built to cooperate.
          </p>
          <blockquote>“Verification is not a gate. It is a conversation with consequences.”</blockquote>
        </div>
      </section>

      <section className="coexistence section-shell" id="the-deal">
        <a className="section-index" href="#the-deal">07 / THE DEAL</a>
        <h2>Humans and robots can coexist.</h2>
        <p>
          But only when both can rely on checks that are powerful, distributed, deterministic—and legible to the people accountable for the outcome.
        </p>
        <div className="coexistence__pair" aria-hidden="true">
          <span>HUMAN</span>
          <i>⇄</i>
          <span>ROBOT</span>
          <small>SHARED GROUND: EVIDENCE</small>
        </div>
      </section>

      <section className="dispatches section-shell" id="open-source" aria-labelledby="dispatches-heading">
        <div className="dispatches__header">
          <div>
            <a className="section-index" href="#open-source">08 / FROM THE LAB</a>
            <h2 id="dispatches-heading">Things we are mutating.</h2>
          </div>
          <span className="dispatches__status"><i /> Public work loading</span>
        </div>
        <div className="dispatch-grid">
          <article className="dispatch-card dispatch-card--featured">
            <p>OPEN SOURCE / IN DEVELOPMENT</p>
            <h3>Deterministic runners for nondeterministic teams.</h3>
            <span>First components are being prepared for release.</span>
            <a href="#contact">Get the signal <b aria-hidden="true">↗</b></a>
          </article>
          <article className="dispatch-card" id="field-notes">
            <p>FIELD NOTE / 001</p>
            <h3>Why agent throughput breaks human-era verification.</h3>
            <span>Research note coming soon.</span>
            <a href="#contact">Follow the work <b aria-hidden="true">↗</b></a>
          </article>
        </div>
      </section>

      <section className="contact section-shell" id="contact">
        <div className="contact__clip">
          <TentacleContactBackground />
          <div className="contact__copy">
            <a className="section-index" href="#contact">CONTACT / NO SMALL TALK REQUIRED</a>
            <h2>Building serious agent infrastructure?</h2>
            <p>We should compare doubts.</p>
            <a className="button button--dark" href="mailto:contact@mutating.tech">
              Start a conversation <span aria-hidden="true">↗</span>
            </a>
          </div>
        </div>
      </section>

      <footer className="site-footer section-shell">
        <p className="site-footer__legal">© <CurrentYear /> THE MUTATING COMPANY.</p>
        <p className="site-footer__tagline">AI systems that doubt. Built for software that has to hold up.</p>
        <div className="site-footer__links">
          <a className="source-link" href="https://github.com/mutating">
            <SourcesLinkLabel />
          </a>
          <a href="#field-notes">Blog</a>
          <a href="#contact">Contact</a>
        </div>
      </footer>
    </main>
  );
}
