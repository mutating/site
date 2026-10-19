export type Cleanup = () => void;

/** Every mounted behavior owns its listeners, observers, and pending work. */
export function createScope(element: HTMLElement) {
  const document = element.ownerDocument;
  const window = document.defaultView;
  if (!window) throw new Error("@mutating/ui requires an attached browser document.");
  const listeners: Cleanup[] = [];
  const cleanups: Cleanup[] = [];
  let disposed = false;
  return {
    document,
    window,
    on<E extends Event = Event>(target: EventTarget, type: string, listener: (event: E) => void, options: AddEventListenerOptions = {}) {
      if (disposed) return;
      const handler = listener as EventListener;
      target.addEventListener(type, handler, options);
      listeners.push(() => target.removeEventListener(type, handler, options));
    },
    get disposed() { return disposed; },
    add(cleanup: Cleanup) { cleanups.push(cleanup); },
    destroy() {
      if (disposed) return;
      disposed = true;
      listeners.reverse().forEach((remove) => remove());
      cleanups.reverse().forEach((cleanup) => cleanup());
    },
  };
}
