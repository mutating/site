export type Cleanup = () => void;

/** Every mounted behavior owns its listeners, observers, and pending work. */
export function createScope(element: HTMLElement) {
  const document = element.ownerDocument;
  const window = document.defaultView;
  if (!window) throw new Error("@mutating/ui requires an attached browser document.");
  const listeners: Cleanup[] = [];
  const cleanups: Cleanup[] = [];
  let disposed = false;
  const on = <E extends Event = Event>(target: EventTarget, type: string, listener: (event: E) => void, options: AddEventListenerOptions = {}) => {
    if (disposed) return;
    const handler = listener as EventListener;
    target.addEventListener(type, handler, options);
    listeners.push(() => target.removeEventListener(type, handler, options));
  };
  return {
    document,
    window,
    on,
    focusAfterFrame(control: HTMLElement) {
      // Firefox can retain the child frame as activeElement on a native click.
      const active = document.activeElement;
      if (active instanceof window.HTMLIFrameElement && !element.contains(active)) {
        active.blur();
        control.focus({ preventScroll: true });
      }
    },
    onFrameFocus(isActive: () => boolean, callback: (frame: HTMLIFrameElement) => void) {
      let checkFrame = 0;
      let previousFocus = document.activeElement;
      const active = () => !disposed && isActive() && element.getClientRects().length > 0
        && !element.closest("details:not([open]), [hidden]");
      const checkFocus = () => {
        const focused = document.activeElement;
        const changed = focused !== previousFocus;
        previousFocus = focused;
        if (changed && focused instanceof window.HTMLIFrameElement) callback(focused);
      };
      const check = () => {
        checkFrame = 0;
        if (!active()) { sync(); return; }
        // Child documents can take focus without any event reaching the parent.
        checkFocus();
        sync();
      };
      const sync = () => {
        if (!active()) {
          window.cancelAnimationFrame(checkFrame);
          checkFrame = 0;
          // An outside frame may have just closed the enclosing menu first.
          if (!disposed && isActive()) checkFocus();
        } else if (!checkFrame) {
          previousFocus = document.activeElement;
          checkFrame = window.requestAnimationFrame(check);
        }
      };
      // Also restart when an enclosing native disclosure reveals this control.
      on(document, "toggle", sync, { capture: true });
      cleanups.push(() => window.cancelAnimationFrame(checkFrame));
      sync();
      return sync;
    },
    get disposed() { return disposed; },
    observeResize(callback: ResizeObserverCallback, ...elements: Element[]) {
      // Size observation enhances layout; unavailable observers must not disable controls.
      try {
        const observer = new window.ResizeObserver(callback);
        cleanups.push(() => observer.disconnect());
        elements.forEach(element => observer.observe(element));
      } catch { /* Window resize and ordinary disclosure events still work. */ }
    },
    add(cleanup: Cleanup) { cleanups.push(cleanup); },
    destroy() {
      if (disposed) return;
      disposed = true;
      listeners.reverse().forEach((remove) => remove());
      cleanups.reverse().forEach((cleanup) => cleanup());
    },
  };
}
