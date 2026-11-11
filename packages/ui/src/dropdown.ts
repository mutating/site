import { createScope, type Cleanup } from "./internal";

/** Keep anchored panels inside the usable viewport, including scrollbars. */
function fitPanelToViewport(panel: HTMLElement) {
  const width = panel.ownerDocument.documentElement.clientWidth;
  const height = panel.ownerDocument.defaultView!.innerHeight;
  panel.style.setProperty("--tmc-dropdown-max-width", `${Math.max(0, width - 24)}px`);
  const bounds = panel.getBoundingClientRect();
  const x = Math.max(12 - bounds.left, Math.min(0, width - 12 - bounds.right));
  const y = Math.max(12 - bounds.top, Math.min(0, height - 12 - bounds.bottom));
  if (x || y) panel.style.setProperty("--tmc-dropdown-shift", `${x}px ${y}px`);
}

export interface DropdownOptions {
  delegateLinks?: boolean;
  closeOnEscape?: boolean;
  closeNestedMenus?: boolean;
  closeOnFocusLeave?: boolean;
  closeOnDesktop?: boolean;
  closeOnSelection?: boolean;
  keyboardNavigation?: boolean;
  /** Keep Escape from also closing an enclosing disclosure such as the mobile menu. */
  stopEscapePropagation?: boolean;
  onOpenChange?: (open: boolean) => void;
}

/** Shared disclosure behavior for the native mobile menu and button-triggered menus. */
export function mountDropdown(root: HTMLElement, options: DropdownOptions = {}): Cleanup {
  const scope = createScope(root);
  const { window, document, on } = scope;
  const native = root instanceof window.HTMLDetailsElement;
  const trigger = () => root.querySelector<HTMLElement>(":scope > summary, :scope > [data-tmc-dropdown-trigger]");
  const panel = root.querySelector<HTMLElement>(":scope > [data-tmc-dropdown-panel], :scope > nav, :scope > .tmc-language-switcher__list");
  const isOpen = () => native ? (root as HTMLDetailsElement).open : root.hasAttribute("data-tmc-open");
  let lastOpen = isOpen();
  const notifyOpenChange = () => {
    const open = isOpen();
    if (open === lastOpen) return;
    lastOpen = open;
    options.onOpenChange?.(open);
  };
  let positionFrame = 0;
  let focusFrame = 0;
  let anchorLeft = NaN;
  let anchorRight = NaN;
  let anchorTop = NaN;
  let anchorBottom = NaN;
  let pendingFocus: HTMLElement | null = null;
  let pointerInside = false;
  const keepFocusedItemVisible = () => {
    const active = document.activeElement;
    if (active instanceof window.HTMLElement && panel?.contains(active)) {
      panel.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
      const item = active.getBoundingClientRect();
      const bounds = panel.getBoundingClientRect();
      const top = Math.max(0, bounds.top + panel.clientTop);
      const bottom = Math.min(window.innerHeight, bounds.top + panel.clientTop + panel.clientHeight);
      panel.scrollTop += item.top < top || item.height > bottom - top ? item.top - top : Math.max(0, item.bottom - bottom);
    }
  };
  const positionPanel = () => {
    if (scope.disposed || !panel || !isOpen()) return;
    panel.style.removeProperty("--tmc-dropdown-shift");
    panel.removeAttribute("data-tmc-above");
    const below = window.innerHeight - panel.getBoundingClientRect().top - 12;
    const above = root.getBoundingClientRect().top - 20;
    const minimum = Number.parseFloat(window.getComputedStyle(panel).minHeight) || 62;
    const openAbove = below < panel.scrollHeight + 2 && above > below && above >= minimum;
    panel.toggleAttribute("data-tmc-above", openAbove);
    panel.style.setProperty("--tmc-dropdown-available-height", `${Math.max(0, Math.min(window.innerHeight - 24, openAbove ? above : below))}px`);
    fitPanelToViewport(panel);
    panel.setAttribute("data-tmc-scrollable", String(panel.scrollHeight > panel.clientHeight));
  };
  const followPanel = () => {
    if (scope.disposed || !isOpen()) { positionFrame = 0; return; }
    const anchor = root.getBoundingClientRect();
    if (anchor.left !== anchorLeft || anchor.right !== anchorRight || anchor.top !== anchorTop || anchor.bottom !== anchorBottom) {
      anchorLeft = anchor.left;
      anchorRight = anchor.right;
      anchorTop = anchor.top;
      anchorBottom = anchor.bottom;
      positionPanel();
    }
    if (pendingFocus && panel) {
      const item = pendingFocus.getBoundingClientRect();
      const bounds = panel.getBoundingClientRect();
      const top = Math.max(0, bounds.top + panel.clientTop);
      const bottom = Math.min(window.innerHeight, bounds.top + panel.clientTop + panel.clientHeight);
      const visible = item.height > bottom - top ? Math.abs(item.top - top) < 1 : item.top >= top && item.bottom <= bottom;
      if (document.activeElement !== pendingFocus || visible) pendingFocus = null;
      else keepFocusedItemVisible();
    }
    positionFrame = window.requestAnimationFrame(followPanel);
  };
  const syncPosition = () => {
    syncFrameFocus();
    if (isOpen() && panel) {
      positionPanel();
      if (!positionFrame) positionFrame = window.requestAnimationFrame(followPanel);
    } else {
      window.cancelAnimationFrame(positionFrame);
      positionFrame = 0;
      pendingFocus = null;
    }
  };
  const setOpen = (open: boolean) => {
    window.cancelAnimationFrame(focusFrame);
    focusFrame = 0;
    if (native) (root as HTMLDetailsElement).open = open;
    else {
      root.toggleAttribute("data-tmc-open", open);
      trigger()?.setAttribute("aria-expanded", String(open));
      if (panel) panel.hidden = !open;
    }
    syncPosition();
    if (!open && options.closeNestedMenus !== false) root.querySelectorAll<HTMLDetailsElement>("details[open]").forEach(child => { child.open = false; });
    notifyOpenChange();
  };
  const close = () => setOpen(false);
  if (panel) {
    const resized = () => {
      const active = document.activeElement;
      pendingFocus = active instanceof window.HTMLElement && panel.contains(active) ? active : null;
      positionPanel();
    };
    on(root, "focusin", () => {
      const active = document.activeElement;
      pendingFocus = active instanceof window.HTMLElement && panel.contains(active) ? active : null;
    });
    on(window, "resize", resized);
    on(document, "scroll", positionPanel, { capture: true, passive: true });
    scope.observeResize(resized, root, panel);
    scope.add(() => {
      window.cancelAnimationFrame(positionFrame);
      panel.style.removeProperty("--tmc-dropdown-shift");
      panel.style.removeProperty("--tmc-dropdown-max-width");
      panel.style.removeProperty("--tmc-dropdown-available-height");
      panel.removeAttribute("data-tmc-above");
      panel.removeAttribute("data-tmc-scrollable");
    });
  }
  if (native) on(root, "toggle", () => { syncPosition(); notifyOpenChange(); });
  if (options.closeOnDesktop) {
    const desktop = window.matchMedia("(min-width: 1121px)");
    on(desktop, "change", () => { if (desktop.matches) close(); });
  }
  const items = () => Array.from((panel ?? root).querySelectorAll<HTMLElement>(
    'a[href], button, [role="menuitem"]',
  )).filter(item => item !== trigger() && !item.matches(':disabled, [aria-disabled="true"]') && item.getClientRects().length > 0);
  on<MouseEvent>(root, "click", event => {
    const button = trigger();
    if (!button || !(event.target instanceof window.Node) || !button.contains(event.target)) return;
    const open = !isOpen();
    scope.focusAfterFrame(button);
    if (native) return;
    setOpen(open);
    if (open && event.detail === 0) items()[0]?.focus();
  });
  if (options.closeOnSelection !== false && options.delegateLinks) {
    on(root, "click", event => {
      const link = event.target instanceof window.Element ? event.target.closest("a") : null;
      if (link && root.contains(link)) close();
    });
  } else if (options.closeOnSelection !== false) root.querySelectorAll("a").forEach(link => on(link, "click", close));
  const press = (event: Event) => {
    pointerInside = event.target instanceof window.Node && root.contains(event.target);
    if (!pointerInside) close();
  };
  on(document, "pointerdown", press);
  // Native link dragging can suppress the next pointerdown in WebKit.
  on(document, "mousedown", press, { capture: true });
  const syncFrameFocus = scope.onFrameFocus(
    isOpen,
    frame => { if (!root.contains(frame)) close(); },
  );
  if (options.closeOnFocusLeave) {
    const releasePointer = () => { pointerInside = false; };
    on(document, "click", releasePointer, { capture: true });
    on(document, "pointercancel", releasePointer);
    on<FocusEvent>(root, "focusout", event => {
      // Disabling a pending action can blur it without moving focus elsewhere.
      // Keep its progress/error feedback visible until the user actually leaves.
      if (!event.relatedTarget && event.target instanceof window.Element && event.target.matches(":disabled")) {
        trigger()?.focus({ preventScroll: true });
        return;
      }
      // Safari blurs keyboard-focused items during an internal pointer press,
      // before delivering its click. Let that selection complete.
      if (!event.relatedTarget && pointerInside) return;
      if (!event.relatedTarget) {
        window.cancelAnimationFrame(focusFrame);
        // Focus crosses documents before activeElement identifies the iframe.
        focusFrame = window.requestAnimationFrame(() => {
          focusFrame = 0;
          if (!pointerInside && !root.contains(document.activeElement)) close();
        });
        return;
      }
      if (!(event.relatedTarget instanceof window.Node) || !root.contains(event.relatedTarget)) close();
    });
    on(document, "focusin", event => {
      if (event.target instanceof window.Node && !root.contains(event.target)) close();
    });
  }
  const keyDown = (event: KeyboardEvent) => {
    if (event.defaultPrevented) return;
    if (options.keyboardNavigation && isOpen() && event.key === " ") {
      const link = event.target instanceof window.Element ? event.target.closest<HTMLElement>('a[href][role="menuitem"]') : null;
      if (link && items().includes(link)) {
        // Links need Space activation when exposed as menuitems. Buttons retain
        // their native keyup activation, so each action still runs only once.
        event.preventDefault();
        link.click();
        return;
      }
    }
    if (options.closeOnEscape !== false && event.key === "Escape" && isOpen()) {
      if (options.stopEscapePropagation) event.stopPropagation();
      if (!native) event.preventDefault();
      close();
      trigger()?.focus();
      return;
    }
    if (!options.keyboardNavigation || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const opening = !isOpen();
    setOpen(true);
    const choices = items();
    const current = choices.indexOf(document.activeElement as HTMLElement);
    const entering = opening || event.target === trigger() || current < 0;
    const last = event.key === "End" || (entering && event.key === "ArrowUp");
    const index = last ? choices.length - 1 : event.key === "Home" || entering ? 0
      : (current + (event.key === "ArrowUp" ? -1 : 1) + choices.length) % choices.length;
    choices[index]?.focus();
    // Firefox can focus a clipped grid item without scrolling the overflow panel.
    choices[index]?.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  on(root, "keydown", keyDown);
  if (!native) on<KeyboardEvent>(document, "keydown", event => {
    // Safari leaves focus on body after pointer activation. Keep a fallback
    // for that case, while handling the menu's own events before outer widgets.
    if (!(event.target instanceof window.Node && root.contains(event.target))
      && isOpen() && document.activeElement === document.body) keyDown(event);
  });
  scope.add(close);
  syncPosition();
  return scope.destroy;
}
