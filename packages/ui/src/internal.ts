export type Cleanup = () => void;

/** Every mounted behavior owns its listeners, observers, and pending work. */
export function createScope(element: HTMLElement) {
  const document = element.ownerDocument;
  const window = document.defaultView;
  if (!window) throw new Error("@mutating/ui requires an attached browser document.");
  const abort = new window.AbortController();
  const cleanups: Cleanup[] = [];
  let disposed = false;
  return {
    document,
    window,
    signal: abort.signal,
    get disposed() { return disposed; },
    add(cleanup: Cleanup) { cleanups.push(cleanup); },
    destroy() {
      if (disposed) return;
      disposed = true;
      abort.abort();
      cleanups.reverse().forEach((cleanup) => cleanup());
    },
  };
}
