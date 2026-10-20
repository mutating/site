import { mountHeader, mountBrand, mountMobileMenu, BRAND_POINTER_EVENT } from "../ui/index.js";

(() => {
  "use strict";

  const TENTACLE_POINTER_RELAY_EVENT = BRAND_POINTER_EVENT;

  function initCurrentYear() {
    const year = String(new Date().getFullYear());
    document.querySelectorAll("[data-current-year]").forEach((node) => {
      node.textContent = year;
    });
  }

  function initCodeCopyButtons() {
    const copyWithFallback = (text) => {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.inset = "0 auto auto -9999px";
      document.body.append(textarea);
      let copied = false;
      try {
        textarea.select();
        copied = document.execCommand("copy");
      } finally {
        textarea.remove();
      }
      if (!copied) throw new Error("The browser rejected the copy command.");
    };

    const copyText = async (text) => {
      if (window.isSecureContext && navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(text);
          return;
        } catch (_) {}
      }
      copyWithFallback(text);
    };

    document.querySelectorAll(".post__content pre").forEach((pre) => {
      const code = pre.querySelector("code");
      if (!code) return;

      let block = pre.parentElement;
      if (!block?.classList.contains("highlight")) {
        block = document.createElement("div");
        pre.before(block);
        block.append(pre);
      }
      block.classList.add("code-block");
      block.dataset.copyHover = "false";
      block.addEventListener("pointerenter", () => {
        block.dataset.copyHover = "true";
    });
      block.addEventListener("pointerleave", () => {
        block.dataset.copyHover = "false";
      });

      const button = document.createElement("button");
      const label = document.createElement("span");
      button.type = "button";
      button.className = "code-copy-button";
      button.setAttribute("aria-label", "Copy code to clipboard");
      button.dataset.copyState = "idle";
      label.textContent = "Copy";
      label.setAttribute("aria-live", "polite");
      button.append(label);
      block.append(button);

      let resetTimer = 0;
      button.addEventListener("click", async () => {
        window.clearTimeout(resetTimer);
        try {
          await copyText(code.textContent || "");
          button.dataset.copyState = "copied";
          button.setAttribute("aria-label", "Code copied to clipboard");
          label.textContent = "Copied";
          resetTimer = window.setTimeout(() => {
            button.dataset.copyState = "idle";
            button.setAttribute("aria-label", "Copy code to clipboard");
            label.textContent = "Copy";
          }, 1800);
        } catch (_) {
          button.dataset.copyState = "error";
          button.setAttribute("aria-label", "Code could not be copied");
          label.textContent = "Try again";
        }
      });
      });
  }

  function initThemeImages() {
    const images = [
      ...document.querySelectorAll(
        "img[data-theme-src-light][data-theme-src-dark]",
      ),
    ];
    if (!images.length) return;

    const synchronize = () => {
      const theme =
        document.documentElement.dataset.theme === "light" ? "light" : "dark";
      images.forEach((image) => {
        const source = image.dataset[
          theme === "light" ? "themeSrcLight" : "themeSrcDark"
        ];
        if (!source) return;
        if (
          image.hasAttribute("data-deferred-src") ||
          !image.hasAttribute("src")
        ) {
          image.dataset.deferredSrc = source;
        } else if (image.getAttribute("src") !== source) {
          image.setAttribute("src", source);
        }
      });
    };

    new MutationObserver(synchronize).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    synchronize();
  }

  function initDeferredMedia() {
    const media = [
      ...document.querySelectorAll("[data-deferred-src]"),
    ];
    if (!media.length) return;

    const load = (element) => {
      const source = element.dataset.deferredSrc;
      if (!source || element.getAttribute("src") === source) return;
      element.setAttribute("src", source);
      delete element.dataset.deferredSrc;
    };

    if (typeof IntersectionObserver !== "function") {
      media.forEach(load);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          observer.unobserve(entry.target);
          load(entry.target);
        });
      },
      { rootMargin: "600px 0px" },
    );
    media.forEach((element) => observer.observe(element));
  }

  function initAnchorAlignment() {
    let cancelled = false;
    let firstFrameRequest = 0;
    let secondFrameRequest = 0;

    const findTarget = () => {
      if (!window.location.hash) return null;
      const encodedId = window.location.hash.slice(1);
      let id = encodedId;
      try {
        id = decodeURIComponent(encodedId);
      } catch (_) {}
      return document.getElementById(id);
    };

    const scrollToCurrentHash = () => {
      if (
        cancelled ||
        window.__tmcAnchorAlignmentInterrupted ||
        !window.location.hash
      ) {
        return;
      }
      const target = findTarget();
      if (!target) return;
      const root = document.documentElement;
      const previousScrollBehavior = root.style.scrollBehavior;
      root.style.scrollBehavior = "auto";
      if (target.id === "top") {
        window.scrollTo({ top: 0, behavior: "auto" });
      } else {
        target.scrollIntoView({ block: "start" });
      }
      root.style.scrollBehavior = previousScrollBehavior;
    };

    const alignCurrentHash = () => {
      if (cancelled || window.__tmcAnchorAlignmentInterrupted) return;
      scrollToCurrentHash();
      if (firstFrameRequest) cancelAnimationFrame(firstFrameRequest);
      if (secondFrameRequest) cancelAnimationFrame(secondFrameRequest);
      firstFrameRequest = requestAnimationFrame(() => {
        scrollToCurrentHash();
        secondFrameRequest = requestAnimationFrame(scrollToCurrentHash);
      });
    };

    const cancelPendingAlignment = () => {
      window.__tmcAnchorAlignmentInterrupted = true;
      if (firstFrameRequest) {
        cancelAnimationFrame(firstFrameRequest);
        firstFrameRequest = 0;
      }
      if (secondFrameRequest) {
        cancelAnimationFrame(secondFrameRequest);
        secondFrameRequest = 0;
      }
    };

    const cancelPendingAlignmentFromKey = (event) => {
      const target = event.target instanceof Element ? event.target : null;
      const isInteractive = target?.closest(
        'a[href], button, input, select, textarea, summary, [contenteditable="true"]',
      );
      if (
        isInteractive &&
        ["ArrowUp", "ArrowDown", "Home", "End", " "].includes(event.key)
      ) {
        return;
      }
      if (
        [
          "ArrowUp",
          "ArrowDown",
          "PageUp",
          "PageDown",
          "Home",
          "End",
          " ",
        ].includes(event.key)
      ) {
        cancelPendingAlignment();
      }
    };

    const cancelPendingAlignmentFromWheel = (event) => {
      if (!event.ctrlKey && Number.isFinite(event.deltaY) && event.deltaY !== 0) {
        cancelPendingAlignment();
      }
    };

    const restartHashAlignment = () => {
      window.__tmcAnchorAlignmentInterrupted = false;
      alignCurrentHash();
    };

    const handlePageShow = (event) => {
      if (event.persisted) restartHashAlignment();
      else alignCurrentHash();
    };

    const handleFrameScrollInput = (event) => {
      const frame = document.querySelector(".contact__tentacles");
      const deltaY = Number(event.data?.deltaY);
      if (
        event.origin === window.location.origin &&
        event.source === frame?.contentWindow &&
        event.data?.type === "muta-tentacles-scroll-input" &&
        Number.isFinite(deltaY) &&
        deltaY !== 0
      ) {
        cancelPendingAlignment();
      }
    };

    const pendingImages = Array.from(document.images).filter(
      (image) => !image.complete,
    );
    pendingImages.forEach((image) => {
      image.addEventListener("load", alignCurrentHash, { once: true });
      image.addEventListener("error", alignCurrentHash, { once: true });
    });
    alignCurrentHash();
    window.addEventListener("load", alignCurrentHash);
    window.addEventListener("pageshow", handlePageShow);
    window.addEventListener("hashchange", restartHashAlignment);
    window.addEventListener("wheel", cancelPendingAlignmentFromWheel, {
      passive: true,
      capture: true,
    });
    window.addEventListener("touchmove", cancelPendingAlignment, {
      passive: true,
      capture: true,
    });
    window.addEventListener("keydown", cancelPendingAlignmentFromKey, true);
    window.addEventListener("message", handleFrameScrollInput);
    document.fonts?.ready.then(alignCurrentHash);
  }

  function initTicker() {
    const ticker = document.querySelector(".ticker");
    const track = ticker?.querySelector(".ticker__track");
    if (!ticker || !track) return;
    const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
    let started = false;
    let frameRequest = 0;
    let animations = [];

    const stop = () => {
      if (frameRequest) {
        cancelAnimationFrame(frameRequest);
        frameRequest = 0;
      }
      animations.forEach((animation) => animation.cancel());
      animations = [];
      started = false;
    };

    const start = () => {
      if (started || reducedMotion.matches) return;
      started = true;
      frameRequest = requestAnimationFrame(() => {
        frameRequest = 0;
        if (reducedMotion.matches) {
          started = false;
          return;
        }
        const trackWidth = track.scrollWidth;
        const tickerWidth = ticker.clientWidth;
        const originalPixelsPerSecond = trackWidth / 56;
        const fullTravelSeconds =
          (trackWidth + tickerWidth) / originalPixelsPerSecond;
        const introTravelSeconds = fullTravelSeconds / 2;
        const pauseSeconds = 20;
        const loopSeconds = fullTravelSeconds + pauseSeconds;
        const travelEndOffset = fullTravelSeconds / loopSeconds;

        const introAnimation = track.animate(
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
        const loopAnimation = track.animate(
          [
            { transform: "translate3d(100vw, 0, 0)", offset: 0 },
            {
              transform: "translate3d(-100%, 0, 0)",
              offset: travelEndOffset,
            },
            { transform: "translate3d(-100%, 0, 0)", offset: 1 },
          ],
          {
            delay: (introTravelSeconds + pauseSeconds) * 1000,
            duration: loopSeconds * 1000,
            easing: "linear",
            iterations: Infinity,
          },
        );
        animations = [introAnimation, loopAnimation];
      });
    };

    reducedMotion.addEventListener("change", () => {
      if (reducedMotion.matches) stop();
      else start();
    });
    if (document.fonts) document.fonts.ready.then(start);
    else start();
  }

  function initComplexityChart() {
    const chart = document.querySelector(".complexity-chart");
    const staticCanvas = chart?.querySelector(".complexity-chart__static");
    const curvesCanvas = chart?.querySelector(".complexity-chart__curves");
    if (!chart || !staticCanvas || !curvesCanvas) return;

    const draw = () => {
      const rect = chart.getBoundingClientRect();
      const dpr = Math.min(devicePixelRatio || 1, 2);
      const width = rect.width;
      const height = 330;
      const pad = { top: 30, right: 24, bottom: 48, left: 0 };
      const plotWidth = width - pad.left - pad.right;
      const plotHeight = height - pad.top - pad.bottom;
      const gridTop = pad.top + plotHeight / 12;
      const styles = getComputedStyle(document.documentElement);
      const grid = styles.getPropertyValue("--line").trim() || "#ddd6dc";
      const hot = styles.getPropertyValue("--hot").trim() || "#ff3e9d";
      const hotSoft =
        styles.getPropertyValue("--hot-soft").trim() || "#b15b87";

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
      for (let index = 0; index <= 4; index += 1) {
        const y = gridTop + (plotHeight / 4) * index;
        staticContext.beginPath();
        staticContext.moveTo(0, y);
        staticContext.lineTo(width, y);
        staticContext.stroke();
      }
      staticContext.setLineDash([]);

      const plot = (fn, color, lineWidth) => {
        curvesContext.beginPath();
        for (let index = 0; index <= 160; index += 1) {
          const progress = index / 160;
          const x = pad.left + progress * plotWidth;
          const value = Math.min(1, fn(progress));
          const y = pad.top + plotHeight - value * plotHeight;
          if (index === 0) curvesContext.moveTo(x, y);
          else curvesContext.lineTo(x, y);
        }
        curvesContext.lineWidth = lineWidth;
        curvesContext.strokeStyle = color;
        curvesContext.lineCap = "round";
        curvesContext.lineJoin = "round";
        curvesContext.stroke();
      };
      plot(
        (x) => (Math.exp(4.35 * x) - 1) / (Math.exp(4.35) - 1),
        hot,
        4,
      );
      plot((x) => 0.06 + 0.28 * x + 0.16 * x * x, hotSoft, 3);

      const verificationLabel = chart.querySelector(
        ".complexity-chart__curve-label--verification",
      );
      if (verificationLabel) {
        const labelRect = verificationLabel.getBoundingClientRect();
        const entropyValueAt = (progress) =>
          (Math.exp(4.35 * progress) - 1) / (Math.exp(4.35) - 1);
        const verificationValueAt = (progress) =>
          0.06 + 0.28 * progress + 0.16 * progress * progress;
        let crossingLow = 0;
        let crossingHigh = 1;
        for (let iteration = 0; iteration < 32; iteration += 1) {
          const crossingMiddle = (crossingLow + crossingHigh) / 2;
          if (
            entropyValueAt(crossingMiddle) <
            verificationValueAt(crossingMiddle)
          ) {
            crossingLow = crossingMiddle;
          } else {
            crossingHigh = crossingMiddle;
          }
        }
        const crossingProgress = (crossingLow + crossingHigh) / 2;
        const crossingX = pad.left + crossingProgress * plotWidth;
        const baseLabelRight = pad.right + 4;
        const baseLabelLeft = width - baseLabelRight - labelRect.width;
        const maximumLabelLeft = width - 4 - labelRect.width;
        const labelLeft = Math.min(
          maximumLabelLeft,
          Math.max(baseLabelLeft, crossingX + 6),
        );
        const labelRight = width - labelLeft - labelRect.width;
        const labelStartProgress = Math.max(
          0,
          Math.min(1, (labelLeft - pad.left) / plotWidth),
        );
        const lowestCurveValue = Math.min(
          entropyValueAt(labelStartProgress),
          verificationValueAt(labelStartProgress),
        );
        const lowestCurveY =
          pad.top + plotHeight - lowestCurveValue * plotHeight;
        const verticalScale = rect.height / height;
        const labelTop = Math.min(
          rect.height - labelRect.height - 4,
          lowestCurveY * verticalScale + 8,
        );
        chart.style.setProperty(
          "--verification-label-right",
          `${labelRight}px`,
        );
        chart.style.setProperty("--verification-label-top", `${labelTop}px`);
        chart.dataset.labelLayoutWidth = width.toFixed(3);
      }
    };

    let hasRevealed = false;
    let revealObserver = null;
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
        innerHeight / Math.max(1, targetHeight),
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
    document.fonts?.ready.then(draw);
    observeForReveal();
    const resizeObserver = new ResizeObserver(() => {
      draw();
      observeForReveal();
    });
    resizeObserver.observe(chart);
    if (visibilityTarget !== chart) resizeObserver.observe(visibilityTarget);
    addEventListener("resize", observeForReveal);
    new MutationObserver(draw).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
  }

  function initFeedbackLoopImpulse() {
    const loop = document.querySelector(".feedback-loop");
    if (!loop) return;

    const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
    const impulseDuration = 1450;
    const impulseStrength = 3.5;
    const maximumBoost = 5;
    let boostAtStart = 0;
    let impulseStartedAt = 0;
    let animationFrame = 0;
    let spinAnimations = [];

    const findSpinAnimations = () => {
      spinAnimations = loop
        .getAnimations({ subtree: true })
        .filter((animation) => animation.animationName === "spin");
      return spinAnimations;
    };

    const setPlaybackRate = (rate) => {
      if (!spinAnimations.length) findSpinAnimations();
      spinAnimations.forEach((animation) => {
        if (typeof animation.updatePlaybackRate === "function") {
          animation.updatePlaybackRate(rate);
        } else {
          animation.playbackRate = rate;
        }
      });
      loop.dataset.playbackRate = rate.toFixed(3);
    };

    const currentBoost = (timestamp) => {
      const progress = Math.max(
        0,
        Math.min(1, (timestamp - impulseStartedAt) / impulseDuration),
      );
      return boostAtStart * (1 - progress) ** 2;
    };

    const settle = () => {
      cancelAnimationFrame(animationFrame);
      animationFrame = 0;
      boostAtStart = 0;
      impulseStartedAt = 0;
      setPlaybackRate(1);
      loop.dataset.impulseActive = "false";
    };

    const animateImpulse = (timestamp) => {
      const boost = currentBoost(timestamp);
      if (boost <= 0.001) {
        settle();
        return;
      }
      setPlaybackRate(1 + boost);
      animationFrame = requestAnimationFrame(animateImpulse);
    };

    const addImpulse = () => {
      if (reducedMotion.matches || !findSpinAnimations().length) return;
      const timestamp = performance.now();
      const remainingBoost = animationFrame ? currentBoost(timestamp) : 0;
      boostAtStart = Math.min(
        maximumBoost,
        remainingBoost + impulseStrength,
      );
      impulseStartedAt = timestamp;
      loop.dataset.impulseActive = "true";
      setPlaybackRate(1 + boostAtStart);
      if (!animationFrame) {
        animationFrame = requestAnimationFrame(animateImpulse);
      }
    };

    loop.dataset.impulseActive = "false";
    loop.dataset.playbackRate = "1.000";
    loop.addEventListener("click", addImpulse);
    loop.addEventListener("keydown", (event) => {
      if (event.repeat) return;
      if (event.key === "Enter") addImpulse();
      if (event.key === " ") event.preventDefault();
    });
    loop.addEventListener("keyup", (event) => {
      if (event.key === " ") addImpulse();
    });
    reducedMotion.addEventListener("change", (event) => {
      if (event.matches) settle();
    });
  }

  function initCoexistencePair() {
    const pair = document.querySelector(".coexistence__pair");
    if (!pair) return;
    pair.addEventListener("click", () => {
      const flipped = pair.classList.toggle("is-flipped");
      pair.setAttribute("aria-pressed", String(flipped));
      pair.setAttribute(
        "aria-label",
        flipped
          ? "ROBOT and HUMAN. Flip to place HUMAN first."
          : "HUMAN and ROBOT. Flip to place ROBOT first.",
      );
      const faces = pair.querySelectorAll(".coexistence__pair-face");
      faces[0]?.setAttribute("aria-hidden", String(flipped));
      faces[1]?.setAttribute("aria-hidden", String(!flipped));
    });
  }

  function initPantograph() {
    const stage = document.querySelector(".pantograph-visual__object");
    if (!stage) return;
    const controller = new AbortController();
    fetch("/pantograph-variant-20-clean-v2.svg", {
      cache: "no-store",
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Pantograph SVG failed to load: ${response.status}`);
        }
        return response.text();
      })
      .then((text) => {
        const source = new DOMParser().parseFromString(text, "image/svg+xml");
        const parseError = source.querySelector("parsererror");
        if (parseError) {
          throw new Error(
            parseError.textContent || "Pantograph SVG is invalid",
          );
        }
        const sourceScript = source.querySelector("script");
        const runtimeCode = sourceScript?.textContent || "";
        sourceScript?.remove();
        const svg = document.importNode(source.documentElement, true);
        svg.removeAttribute("width");
        svg.removeAttribute("height");
        stage.replaceChildren(svg);
        if (runtimeCode) {
          const runtime = document.createElement("script");
          runtime.textContent = runtimeCode;
          stage.append(runtime);
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          stage.dataset.pantographError =
            error instanceof Error ? error.message : String(error);
        }
      });
  }

  function initTokamak() {
    const shell = document.querySelector(".tokamak-model");
    const frame = shell?.querySelector(".tokamak-model__frame");
    if (!shell || !frame) return;
    const edgeOnRotation = -Math.atan2(9.42, 16.5);
    let frameRequest = 0;

    const updateRotation = () => {
      frameRequest = 0;
      const rect = shell.getBoundingClientRect();
      const visible =
        !document.hidden &&
        rect.bottom >= -200 &&
        rect.top <= innerHeight + 200;
      const distanceFromEntryToCenter = (innerHeight + rect.height) / 2;
      const scrollProgress = Math.min(
        2,
        Math.max(0, (innerHeight - rect.top) / distanceFromEntryToCenter),
      );
      const rotationX = edgeOnRotation * scrollProgress;
      const renderedHeight = Math.min(600, Math.max(1, rect.height));
      const heightScale = 1 / (1 + (600 - renderedHeight) * 0.00125);
      const aspectScale = Math.min(
        1,
        (Math.max(1, rect.width) / renderedHeight) * 1.1,
      );
      const modelScale = Math.min(heightScale, aspectScale);
      const theme =
        document.documentElement.dataset.theme === "dark" ? "dark" : "light";
      shell.dataset.scrollProgress = scrollProgress.toFixed(3);
      shell.dataset.rotationX = rotationX.toFixed(3);
      shell.dataset.modelScale = modelScale.toFixed(3);
      frame.contentWindow?.postMessage(
        { type: "tokamak-state", rotationX, modelScale, theme, visible },
        location.origin,
      );
    };
    const schedule = () => {
      if (!frameRequest) frameRequest = requestAnimationFrame(updateRotation);
    };
    const handleScroll = () => {
      if (frameRequest) {
        cancelAnimationFrame(frameRequest);
        frameRequest = 0;
      }
      updateRotation();
    };
    new ResizeObserver(schedule).observe(shell);
    new MutationObserver(schedule).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    new IntersectionObserver(
      () => schedule(),
      { rootMargin: "200px 0px" },
    ).observe(shell);
    frame.addEventListener("load", schedule);
    document.addEventListener("visibilitychange", schedule);
    addEventListener("message", (event) => {
      if (
        event.source !== frame.contentWindow ||
        event.origin !== location.origin
      ) {
        return;
      }
      if (event.data?.type === "tokamak-ready") schedule();
      else if (event.data?.type === "tokamak-state-applied") {
        shell.classList.add("is-ready");
      }
    });
    addEventListener("scroll", handleScroll, { passive: true });
    addEventListener("resize", schedule);
    schedule();
  }

  function initTentacles() {
    const frame = document.querySelector(".contact__tentacles");
    if (!frame) return;
    const contact = frame.closest(".contact");
    const copy = contact?.querySelector(".contact__copy");
    copy?.querySelectorAll("h2, p:not(.section-index)").forEach((textBlock) => {
      if (textBlock.querySelector(":scope > .contact__selectable-text")) return;
      const selectableText = document.createElement("span");
      selectableText.className = "contact__selectable-text";
      selectableText.append(...textBlock.childNodes);
      textBlock.append(selectableText);
    });
    let contactIsNearViewport = false;
    let contactIsVisible = false;
    let scrollIsActive = false;
    let scrollIdleTimer = 0;
    let pointerRefreshFrame = 0;
    let hasViewportPointer = false;
    let lastPointerClientX = 0;
    let lastPointerClientY = 0;
    const scrollIdleFallbackMs = 320;

    const prewarm = () => {
      const source = frame.dataset.prewarmSrc;
      if (
        !source ||
        frame.hasAttribute("src") ||
        frame.hasAttribute("srcdoc")
      ) {
        return;
      }
      frame.loading = "eager";
      frame.src = source;
      delete frame.dataset.prewarmSrc;
    };
    const schedulePrewarm = () => {
      requestAnimationFrame(() => {
        if ("requestIdleCallback" in window) {
          window.requestIdleCallback(prewarm, { timeout: 2000 });
        } else {
          window.setTimeout(prewarm, 0);
        }
      });
    };

    const sendTheme = () => {
      const theme =
        document.documentElement.dataset.theme === "dark" ? "dark" : "light";
      frame.contentWindow?.postMessage(
        { type: "muta-tentacles-theme", theme },
        location.origin,
      );
    };
    const sendRuntimeState = () => {
      const state = {
        visible: contactIsVisible && !document.hidden,
        scrollActive: scrollIsActive,
      };
      frame.dataset.scrollActive = String(scrollIsActive);
      const runtimeWindow = frame.contentWindow;
      try {
        if (runtimeWindow?.__mutaTentaclesSetRuntimeState) {
          runtimeWindow.__mutaTentaclesSetRuntimeState(state);
          return;
        }
      } catch (_) {}
      runtimeWindow?.postMessage(
        { type: "muta-tentacles-runtime", ...state },
        location.origin,
      );
    };
    const releaseTentacles = () => {
      frame.contentWindow?.postMessage(
        { type: "muta-tentacles-release" },
        location.origin,
      );
    };
    const releaseTentaclesAfterBlur = () => {
      setTimeout(() => {
        // The first click inside an iframe blurs its parent window while making
        // the iframe the active element. That is an internal focus transfer,
        // not a pointer exit; the child will handle its own pointerup.
        if (document.activeElement === frame) return;
        releaseTentacles();
      }, 0);
    };
    const rememberViewportPointer = (clientX, clientY) => {
      const numericX = Number(clientX);
      const numericY = Number(clientY);
      if (!Number.isFinite(numericX) || !Number.isFinite(numericY)) {
        return false;
      }
      lastPointerClientX = numericX;
      lastPointerClientY = numericY;
      hasViewportPointer = true;
      return true;
    };
    const refreshRelayedPointer = () => {
      pointerRefreshFrame = 0;
      if (!hasViewportPointer) return;
      const rect = frame.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      const inside =
        lastPointerClientX >= rect.left &&
        lastPointerClientX <= rect.right &&
        lastPointerClientY >= rect.top &&
        lastPointerClientY <= rect.bottom;
      frame.contentWindow?.postMessage(
        inside
          ? {
              type: "muta-tentacles-pointer-hover",
              x: Math.max(
                0,
                Math.min(
                  1,
                  (lastPointerClientX - rect.left) / rect.width,
                ),
              ),
              y: Math.max(
                0,
                Math.min(
                  1,
                  (lastPointerClientY - rect.top) / rect.height,
                ),
              ),
              inside: true,
            }
          : {
              type: "muta-tentacles-pointer-hover",
              inside: false,
            },
        location.origin,
      );
    };
    const schedulePointerRefresh = () => {
      if (pointerRefreshFrame || !hasViewportPointer) return;
      pointerRefreshFrame = requestAnimationFrame(refreshRelayedPointer);
    };
    const relayPassivePointer = (event) => {
      if (!rememberViewportPointer(event.clientX, event.clientY)) return;
      if (pointerRefreshFrame) {
        cancelAnimationFrame(pointerRefreshFrame);
        pointerRefreshFrame = 0;
      }
      refreshRelayedPointer();
    };
    const clearRelayedPointer = () => {
      frame.contentWindow?.postMessage(
        { type: "muta-tentacles-pointer-hover", inside: false },
        location.origin,
      );
    };
    const clearScrollIdleTimer = () => {
      if (!scrollIdleTimer) return;
      clearTimeout(scrollIdleTimer);
      scrollIdleTimer = 0;
    };
    const finishScroll = () => {
      clearScrollIdleTimer();
      schedulePointerRefresh();
      if (!scrollIsActive) return;
      scrollIsActive = false;
      sendRuntimeState();
    };
    const cancelBoundaryScroll = () => {
      clearScrollIdleTimer();
      scrollIsActive = false;
      sendRuntimeState();
    };
    const canPageScrollInDirection = (deltaY) => {
      if (!Number.isFinite(deltaY) || deltaY === 0) return false;
      const root = document.scrollingElement ?? document.documentElement;
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
      if (!contactIsVisible) return;
      schedulePointerRefresh();
      if (!scrollIsActive) {
        scrollIsActive = true;
        sendRuntimeState();
      }
      if (scrollIdleTimer) clearTimeout(scrollIdleTimer);
      scrollIdleTimer = setTimeout(finishScroll, scrollIdleFallbackMs);
    };
    const handleForwardedScrollInput = (deltaY) => {
      const numericDeltaY = Number(deltaY);
      if (!Number.isFinite(numericDeltaY) || numericDeltaY === 0) return;
      if (!canPageScrollInDirection(numericDeltaY)) {
        cancelBoundaryScroll();
        return;
      }
      handleScroll();
    };
    const handleMessage = (event) => {
      if (
        event.source !== frame.contentWindow ||
        event.origin !== location.origin
      ) {
        return;
      }
      if (event.data?.type === "muta-tentacles-ready") {
        sendTheme();
        sendRuntimeState();
      } else if (event.data?.type === "muta-tentacles-scroll-input") {
        handleForwardedScrollInput(event.data.deltaY);
      } else if (event.data?.type === "muta-tentacles-pointer-move") {
        const normalizedX = Number(event.data.x);
        const normalizedY = Number(event.data.y);
        if (
          !Number.isFinite(normalizedX) ||
          !Number.isFinite(normalizedY)
        ) {
          return;
        }
        const rect = frame.getBoundingClientRect();
        const clientX =
          rect.left +
          Math.max(0, Math.min(1, normalizedX)) * rect.width;
        const clientY =
          rect.top +
          Math.max(0, Math.min(1, normalizedY)) * rect.height;
        rememberViewportPointer(clientX, clientY);
        dispatchEvent(
          new CustomEvent(TENTACLE_POINTER_RELAY_EVENT, {
            detail: {
              clientX,
              clientY,
            },
          }),
        );
      }
    };

    new MutationObserver(sendTheme).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    new IntersectionObserver(
      ([entry]) => {
        contactIsNearViewport = entry.isIntersecting;
        if (contactIsNearViewport) {
          prewarm();
        }
      },
      { rootMargin: "200px 0px" },
    ).observe(frame);
    new IntersectionObserver(
      ([entry]) => {
        contactIsVisible = entry.isIntersecting;
        if (!contactIsVisible) {
          clearScrollIdleTimer();
          scrollIsActive = false;
        } else {
          schedulePointerRefresh();
        }
        sendRuntimeState();
      },
    ).observe(frame);
    frame.addEventListener("load", sendTheme);
    copy?.addEventListener("pointermove", relayPassivePointer, {
      passive: true,
    });
    addEventListener(
      "pointermove",
      (event) => {
        rememberViewportPointer(event.clientX, event.clientY);
      },
      { passive: true },
    );
    contact?.addEventListener("pointerleave", clearRelayedPointer, {
      passive: true,
    });
    addEventListener("message", handleMessage);
    addEventListener("scroll", handleScroll, { passive: true });
    addEventListener("scrollend", finishScroll, { passive: true });
    addEventListener("resize", schedulePointerRefresh, { passive: true });
    addEventListener("pointerup", releaseTentacles, { passive: true });
    addEventListener("pointercancel", releaseTentacles, { passive: true });
    addEventListener("blur", releaseTentaclesAfterBlur);
    addEventListener("pagehide", releaseTentacles);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) releaseTentacles();
      sendRuntimeState();
    });
    sendTheme();
    sendRuntimeState();
    schedulePrewarm();
  }

  function initPostToc() {
    const toc = document.querySelector(".post__toc");
    if (!toc) return;

    const desktopMedia = matchMedia("(min-width: 1101px)");
    const syncTocState = () => {
      if (desktopMedia.matches) toc.setAttribute("open", "");
      else toc.removeAttribute("open");
    };

    syncTocState();
    desktopMedia.addEventListener("change", syncTocState);
    toc.addEventListener("toggle", () => {
      if (desktopMedia.matches && !toc.open) toc.setAttribute("open", "");
    });
  }

  function initUnpaginatedBlogTail() {
    const index = document.querySelector(
      '.blog-index[data-has-pagination="false"]',
    );
    const lastCard = index?.querySelector(".blog-card:last-child");
    if (!index || !lastCard) return;

    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        index.dataset.pageOverflows = "false";
        const borderWidth = Number.parseFloat(
          getComputedStyle(lastCard).borderBottomWidth,
        );
        const heightWithoutTailBorder =
          document.documentElement.scrollHeight - borderWidth;
        index.dataset.pageOverflows = String(
          heightWithoutTailBorder > innerHeight + 0.5,
        );
      });
    };

    update();
    addEventListener("resize", update, { passive: true });
    document.fonts?.ready.then(update);
  }

  function init() {
    initCurrentYear();
    initCodeCopyButtons();
    const header = document.querySelector(".tmc-header-shell");
    if (header) mountHeader(header, {
      themeOptions: {
        // Keep the theme already painted by the head while this bundle loaded.
        initialTheme: document.documentElement.dataset.theme === "light" ? "light" : "dark",
        syncAcrossTabs: false,
        preferenceSource: "storage",
        cookieDomain: "mutating.tech",
      },
      language: { closeOnSelection: false, stopEscapePropagation: false },
      themeToggle: { exposePressedState: false },
      brand: false,
      mobileMenu: false,
    });
    initThemeImages();
    initDeferredMedia();
    initTicker();
    header?.querySelectorAll(".tmc-brand").forEach((brand) => mountBrand(brand));
    initComplexityChart();
    initFeedbackLoopImpulse();
    initCoexistencePair();
    initPantograph();
    initTokamak();
    initTentacles();
    header?.querySelectorAll(".tmc-mobile-menu").forEach((menu) => mountMobileMenu(menu, {
      closeOnEscape: false, closeOnDesktop: false, closeNestedMenus: false,
    }));
    initPostToc();
    initUnpaginatedBlogTail();
    document.documentElement.dataset.themeReady = "true";
  }

  // A deferred script runs after the document is parsed and before
  // DOMContentLoaded. Align fragments synchronously in that phase so deep links
  // are already at their final position when DOMContentLoaded is observable.
  initAnchorAlignment();

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
