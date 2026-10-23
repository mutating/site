import { createScope, type Cleanup } from "./internal";

export interface DropdownOptions {
  delegateLinks?: boolean;
  closeOnEscape?: boolean;
  closeNestedMenus?: boolean;
  closeOnFocusLeave?: boolean;
  closeOnDesktop?: boolean;
  keyboardNavigation?: boolean;
  onOpenChange?: (open: boolean) => void;
}

/** Shared disclosure behavior for the native mobile menu and button-triggered menus. */
export function mountDropdown(root: HTMLElement, options: DropdownOptions = {}): Cleanup {
  const scope = createScope(root);
  const { window, document, on } = scope;
  const native = root instanceof window.HTMLDetailsElement;
  const trigger = () => root.querySelector<HTMLElement>(":scope > summary, :scope > [data-tmc-dropdown-trigger]");
  const panel = root.querySelector<HTMLElement>(":scope > [data-tmc-dropdown-panel]");
  const isOpen = () => native ? (root as HTMLDetailsElement).open : root.hasAttribute("data-tmc-open");
  let positionFrame = 0;
  let anchorLeft = NaN;
  let anchorRight = NaN;
  const positionPanel = () => {
    if (scope.disposed || native || !panel || !isOpen()) return;
    panel.style.removeProperty("--tmc-dropdown-shift");
    const bounds = panel.getBoundingClientRect();
    const shift = Math.max(12 - bounds.left, Math.min(0, document.documentElement.clientWidth - 12 - bounds.right));
    if (shift) panel.style.setProperty("--tmc-dropdown-shift", `${shift}px`);
  };
  const followPanel = () => {
    if (scope.disposed || !isOpen()) { positionFrame = 0; return; }
    const anchor = root.getBoundingClientRect();
    if (anchor.left !== anchorLeft || anchor.right !== anchorRight) {
      anchorLeft = anchor.left;
      anchorRight = anchor.right;
      positionPanel();
    }
    positionFrame = window.requestAnimationFrame(followPanel);
  };
  const setOpen = (open: boolean) => {
    if (native) (root as HTMLDetailsElement).open = open;
    else {
      root.toggleAttribute("data-tmc-open", open);
      trigger()?.setAttribute("aria-expanded", String(open));
      if (panel) panel.hidden = !open;
      if (open && panel) {
        positionPanel();
        if (!positionFrame) positionFrame = window.requestAnimationFrame(followPanel);
      } else {
        window.cancelAnimationFrame(positionFrame);
        positionFrame = 0;
      }
    }
    if (!open && options.closeNestedMenus !== false) root.querySelectorAll<HTMLDetailsElement>("details[open]").forEach(child => { child.open = false; });
    options.onOpenChange?.(open);
  };
  const close = () => setOpen(false);
  if (!native && panel) {
    on(window, "resize", positionPanel);
    on(document, "scroll", positionPanel, { capture: true, passive: true });
    const observer = new window.ResizeObserver(positionPanel);
    observer.observe(root);
    observer.observe(panel);
    scope.add(() => {
      observer.disconnect();
      window.cancelAnimationFrame(positionFrame);
      panel.style.removeProperty("--tmc-dropdown-shift");
    });
  }
  if (options.closeOnDesktop) {
    const desktop = window.matchMedia("(min-width: 1121px)");
    on(desktop, "change", () => { if (desktop.matches) close(); });
  }
  const items = () => Array.from((panel ?? root).querySelectorAll<HTMLElement>(
    'a[href], button, [role="menuitem"]',
  )).filter(item => item !== trigger() && !item.matches(':disabled, [aria-disabled="true"]') && item.getClientRects().length > 0);
  if (!native) on<MouseEvent>(root, "click", event => {
    const button = trigger();
    if (!button || !(event.target instanceof window.Node) || !button.contains(event.target)) return;
    const open = !isOpen();
    setOpen(open);
    if (open && event.detail === 0) items()[0]?.focus();
  });
  if (options.delegateLinks) {
    on(root, "click", event => {
      const link = event.target instanceof window.Element ? event.target.closest("a") : null;
      if (link && root.contains(link)) close();
    });
  } else root.querySelectorAll("a").forEach(link => on(link, "click", close));
  on(document, "pointerdown", event => {
    if (event.target instanceof window.Node && !root.contains(event.target)) close();
  });
  if (options.closeOnFocusLeave) {
    on<FocusEvent>(root, "focusout", event => {
      // Disabling a pending action can blur it without moving focus elsewhere.
      // Keep its progress/error feedback visible until the user actually leaves.
      if (!event.relatedTarget && event.target instanceof window.Element && event.target.matches(":disabled")) {
        trigger()?.focus({ preventScroll: true });
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
  };
  on(root, "keydown", keyDown);
  if (!native) on<KeyboardEvent>(document, "keydown", event => {
    // Safari leaves focus on body after pointer activation. Keep a fallback
    // for that case, while handling the menu's own events before outer widgets.
    if (!(event.target instanceof window.Node && root.contains(event.target))
      && isOpen() && document.activeElement === document.body) keyDown(event);
  });
  scope.add(close);
  if (!native && panel && isOpen()) {
    positionPanel();
    positionFrame = window.requestAnimationFrame(followPanel);
  }
  return scope.destroy;
}
