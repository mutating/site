(() => {
  "use strict";

  const THEME_TOGGLE_BLINK_EVENT = "tmc-theme-toggle-blink";
  const BRAND_HOME_ACTIVATE_EVENT = "tmc-brand-home-activate";
  const BRAND_HOME_RELOAD_DELAY_MS = 260;
  const TENTACLE_POINTER_RELAY_EVENT = "tmc-tentacle-pointer-move";

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
      textarea.select();
      const copied = document.execCommand("copy");
      textarea.remove();
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

  function initTheme() {
    const darkMedia = window.matchMedia("(prefers-color-scheme: dark)");
    const lightMedia = window.matchMedia("(prefers-color-scheme: light)");

    const syncSystemTheme = () => {
      let storedTheme = null;
      try {
        storedTheme = window.localStorage.getItem("tmc-theme");
      } catch (_) {}
      if (storedTheme === "dark" || storedTheme === "light") return;
      document.documentElement.dataset.theme = darkMedia.matches
        ? "dark"
        : lightMedia.matches
          ? "light"
          : "dark";
    };

    darkMedia.addEventListener("change", syncSystemTheme);
    lightMedia.addEventListener("change", syncSystemTheme);
    document.querySelectorAll(".theme-toggle").forEach((toggle) => {
      toggle.addEventListener("click", () => {
        const current =
          document.documentElement.dataset.theme === "light"
            ? "light"
            : "dark";
        const next = current === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        try {
          window.localStorage.setItem("tmc-theme", next);
        } catch (_) {}
        window.dispatchEvent(new Event(THEME_TOGGLE_BLINK_EVENT));
      });
    });
  }

  function initLanguageSwitcher() {
    const switchers = [
      ...document.querySelectorAll("[data-language-switcher]"),
    ];
    if (!switchers.length) return;

    const desktopLayout = matchMedia("(min-width: 1041px)");
    const desktopHover = matchMedia(
      "(min-width: 1041px) and (hover: hover) and (pointer: fine)",
    );
    const close = (switcher) => switcher.removeAttribute("open");

    switchers.forEach((switcher) => {
      const summary = switcher.querySelector(":scope > summary");
      const isDesktop = switcher.classList.contains(
        "language-switcher--desktop",
      );

      if (isDesktop) {
        switcher.addEventListener("pointerenter", () => {
          if (desktopHover.matches) switcher.setAttribute("open", "");
        });
        switcher.addEventListener("pointerleave", () => {
          if (desktopHover.matches) close(switcher);
        });
        summary?.addEventListener("click", (event) => {
          if (!desktopHover.matches || event.detail === 0) return;
          event.preventDefault();
          switcher.setAttribute("open", "");
        });
      }

      switcher.addEventListener("keydown", (event) => {
        if (event.key !== "Escape" || !switcher.open) return;
        close(switcher);
        summary?.focus();
      });

      switcher.querySelectorAll("[data-language-option]").forEach((link) => {
        link.addEventListener("click", () => {
          const language = link.dataset.languageOption;
          if (!language) return;
          try {
            window.localStorage.setItem("tmc-language", language);
          } catch (_) {}
        });
      });
    });

    const synchronizeLayout = () => {
      switchers.forEach((switcher) => {
        const hiddenByLayout = desktopLayout.matches
          ? switcher.classList.contains("language-switcher--mobile")
          : switcher.classList.contains("language-switcher--desktop");
        if (hiddenByLayout) close(switcher);
      });
    };

    document.addEventListener("pointerdown", (event) => {
      const clickedSwitcher =
        event.target instanceof Element
          ? event.target.closest("[data-language-switcher]")
          : null;

      switchers.forEach((switcher) => {
        if (switcher.open && switcher !== clickedSwitcher) close(switcher);
      });
    });
    desktopLayout.addEventListener("change", synchronizeLayout);
    synchronizeLayout();
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

  function initAnimatedWordmark() {
    const stage = document.querySelector(".brand__wordmark-stage");
    const brand = stage?.closest(".brand");
    const header = stage?.closest(".site-header");
    const actions = header?.querySelector(".header-actions");
    const eye = brand?.querySelector(".brand__mark--eye");
    if (!stage || !brand || !header || !actions || !eye) return;
    let frameRequest = 0;

    const update = () => {
      cancelAnimationFrame(frameRequest);
      frameRequest = requestAnimationFrame(() => {
        if (matchMedia("(min-width: 921px)").matches) {
          stage.dataset.canExpand = "true";
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
        const expandedStageWidth =
          (Number.parseFloat(stageStyle.fontSize) || 0) * 11.6;
        const requiredWidth =
          eyeRect.width + eyeMargins + brandGap + expandedStageWidth;
        const availableWidth =
          headerRect.width -
          (Number.parseFloat(headerStyle.paddingLeft) || 0) -
          (Number.parseFloat(headerStyle.paddingRight) || 0) -
          actionsRect.width;
        stage.dataset.canExpand =
          availableWidth + 0.5 >= requiredWidth ? "true" : "false";
        stage.dataset.expansionMeasured = "true";
        stage.dataset.expansionRevision = String(
          Number(stage.dataset.expansionRevision || 0) + 1,
        );
      });
    };

    const resizeObserver = new ResizeObserver(update);
    resizeObserver.observe(header);
    resizeObserver.observe(actions);
    window.addEventListener("resize", update);
    update();
    document.fonts?.ready.then(update);
  }

  function initBrandControl() {
    const brand = document.querySelector(".brand");
    if (!brand) return;
    let scheduled = false;
    const activate = () => {
      if (scheduled) return;
      scheduled = true;
      const homeUrl = brand.dataset.homeUrl || "/";
      const currentPath = window.location.pathname.replace(/\/+$/, "") || "/";
      const normalizedHome = new URL(homeUrl, location.href).pathname
        .replace(/\/+$/, "") || "/";
      if (currentPath !== normalizedHome) {
        window.location.assign(homeUrl);
        return;
      }
      const hashlessUrl = `${window.location.pathname}${window.location.search}`;
      history.replaceState(history.state, "", hashlessUrl);
      dispatchEvent(new Event(BRAND_HOME_ACTIVATE_EVENT));
      setTimeout(() => location.reload(), BRAND_HOME_RELOAD_DELAY_MS);
    };
    brand.addEventListener("click", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      activate();
    });
    brand.addEventListener("keydown", (event) => {
      if (event.repeat || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault();
      activate();
    });
  }

  function initAnimatedEye() {
    const eye = document.querySelector(".animated-eye-logo");
    const eyeClip = eye?.querySelector(".animated-eye-logo__clip");
    const pupil = eye?.querySelector(".animated-eye-logo__pupil");
    if (!eye || !eyeClip || !pupil) return;

    const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
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
    let scrollFrame = 0;
    let previousGazeFrameAt = 0;

    const renderScrollTone = () => {
      scrollFrame = 0;
      const scrollRange = Math.max(1, innerHeight);
      const whiteness = Math.max(0, Math.min(1, scrollY / scrollRange));
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
        const closed = `path("M 0 ${middle.toFixed(3)} L ${width.toFixed(3)} ${middle.toFixed(3)} Z")`;
        eyeClip.style.clipPath = closed;
        eyeClip.style.webkitClipPath = closed;
        return;
      }
      const halfChord = width / 2;
      const sagitta = middle * openness;
      const radius =
        (halfChord * halfChord + sagitta * sagitta) / (2 * sagitta);
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
    const aimAt = (clientX, clientY) => {
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
        distance / (Math.min(innerWidth, innerHeight) * 0.34 + 40),
      );
      gaze.targetX = ux * boundary * reach;
      gaze.targetY = uy * boundary * reach;
      if (reduceMotion) {
        gaze.x = gaze.targetX;
        gaze.y = gaze.targetY;
        renderGaze();
      }
    };
    const centerGaze = () => {
      gaze.targetX = 0;
      gaze.targetY = 0;
    };
    const smoothStep = (value) => value * value * (3 - 2 * value);
    const transitionLid = (target, baseDuration, kind) => {
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

    const updateLid = (now) => {
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
    const advanceGaze = (elapsedFrames) => {
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
    const animate = (now) => {
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
    new ResizeObserver(updateBounds).observe(eye);
    addEventListener(
      "scroll",
      () => {
        if (!scrollFrame) scrollFrame = requestAnimationFrame(renderScrollTone);
      },
      { passive: true },
    );
    addEventListener(
      "resize",
      () => {
        updateBounds();
        renderScrollTone();
      },
      { passive: true },
    );
    addEventListener("pointermove", (event) => aimAt(event.clientX, event.clientY), {
      passive: true,
    });
    addEventListener("pointerdown", (event) => aimAt(event.clientX, event.clientY), {
      passive: true,
    });
    addEventListener(TENTACLE_POINTER_RELAY_EVENT, (event) => {
      const clientX = Number(event.detail?.clientX);
      const clientY = Number(event.detail?.clientY);
      if (Number.isFinite(clientX) && Number.isFinite(clientY)) {
        aimAt(clientX, clientY);
      }
    });
    addEventListener("blur", centerGaze);
    addEventListener(THEME_TOGGLE_BLINK_EVENT, blinkForTheme);
    addEventListener(BRAND_HOME_ACTIVATE_EVENT, holdClosed);
    document.addEventListener("pointerout", (event) => {
      if (!event.relatedTarget) centerGaze();
    });
    setInterval(blink, 30000);
    animationFrame = requestAnimationFrame(animate);
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
      if (event.source !== frame.contentWindow) return;
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
    let scrollIsActive = false;
    let scrollIdleTimer = 0;
    const scrollIdleFallbackMs = 320;

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
        visible: contactIsNearViewport && !document.hidden,
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
    const relayPassivePointer = (event) => {
      const rect = frame.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      frame.contentWindow?.postMessage(
        {
          type: "muta-tentacles-pointer-hover",
          x: Math.max(
            0,
            Math.min(1, (event.clientX - rect.left) / rect.width),
          ),
          y: Math.max(
            0,
            Math.min(1, (event.clientY - rect.top) / rect.height),
          ),
          inside: true,
        },
        location.origin,
      );
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
      if (!contactIsNearViewport) return;
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
        dispatchEvent(
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

    new MutationObserver(sendTheme).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    new IntersectionObserver(
      ([entry]) => {
        contactIsNearViewport = entry.isIntersecting;
        if (!contactIsNearViewport) {
          clearScrollIdleTimer();
          scrollIsActive = false;
        }
        sendRuntimeState();
      },
      { rootMargin: "200px 0px" },
    ).observe(frame);
    frame.addEventListener("load", sendTheme);
    copy?.addEventListener("pointermove", relayPassivePointer, {
      passive: true,
    });
    contact?.addEventListener("pointerleave", clearRelayedPointer, {
      passive: true,
    });
    addEventListener("message", handleMessage);
    addEventListener("scroll", handleScroll, { passive: true });
    addEventListener("scrollend", finishScroll, { passive: true });
    addEventListener("pointerup", releaseTentacles, { passive: true });
    addEventListener("pointercancel", releaseTentacles, { passive: true });
    addEventListener("blur", releaseTentacles);
    addEventListener("pagehide", releaseTentacles);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) releaseTentacles();
      sendRuntimeState();
    });
    sendTheme();
    sendRuntimeState();
  }

  function initMobileMenus() {
    const menus = [...document.querySelectorAll(".mobile-menu")];

    menus.forEach((menu) => {
      menu.querySelectorAll("nav a").forEach((link) => {
        link.addEventListener("click", () => {
          menu.removeAttribute("open");
        });
      });
    });

    document.addEventListener("pointerdown", (event) => {
      const clickedMenu =
        event.target instanceof Element
          ? event.target.closest(".mobile-menu")
          : null;

      menus.forEach((menu) => {
        if (menu.open && menu !== clickedMenu) {
          menu.removeAttribute("open");
        }
      });
    });
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
    initTheme();
    initLanguageSwitcher();
    initThemeImages();
    initDeferredMedia();
    initTicker();
    initAnimatedWordmark();
    initBrandControl();
    initAnimatedEye();
    initComplexityChart();
    initFeedbackLoopImpulse();
    initCoexistencePair();
    initPantograph();
    initTokamak();
    initTentacles();
    initMobileMenus();
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
