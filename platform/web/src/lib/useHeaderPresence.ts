import { useLayoutEffect, useRef, useState, type RefObject } from "react";

/** Keep controls under an active press in place while the session resolves. */
export function useHeaderPresence(header: RefObject<HTMLElement | null>, present: boolean) {
  const [visible, setVisible] = useState(present);
  const desired = useRef(present);
  const presses = useRef(new Set<string>());
  useLayoutEffect(() => {
    desired.current = present;
    if (!presses.current.size) setVisible(present);
  }, [present]);
  useLayoutEffect(() => {
    const root = header.current!;
    const document = root.ownerDocument;
    const window = document.defaultView!;
    let releaseFrame = 0;
    const release = (key: string) => {
      presses.current.delete(key);
      // Touch and Space synthesize their click after pointerup/keyup. Let that
      // default action use the original geometry before publishing the new slot.
      window.cancelAnimationFrame(releaseFrame);
      releaseFrame = window.requestAnimationFrame(() => {
        if (!presses.current.size) setVisible(desired.current);
      });
    };
    const pointerDown = (event: PointerEvent) => { presses.current.add(`pointer:${event.pointerId}`); };
    const pointerUp = (event: PointerEvent) => release(`pointer:${event.pointerId}`);
    const keyDown = (event: KeyboardEvent) => {
      if (event.key === "Enter" || event.key === " ") presses.current.add(`key:${event.code}`);
    };
    const keyUp = (event: KeyboardEvent) => release(`key:${event.code}`);
    const reset = () => { presses.current.clear(); setVisible(desired.current); };
    root.addEventListener("pointerdown", pointerDown);
    root.addEventListener("keydown", keyDown);
    document.addEventListener("pointerup", pointerUp);
    document.addEventListener("pointercancel", pointerUp);
    document.addEventListener("keyup", keyUp);
    window.addEventListener("blur", reset);
    return () => {
      root.removeEventListener("pointerdown", pointerDown);
      root.removeEventListener("keydown", keyDown);
      document.removeEventListener("pointerup", pointerUp);
      document.removeEventListener("pointercancel", pointerUp);
      document.removeEventListener("keyup", keyUp);
      window.removeEventListener("blur", reset);
      window.cancelAnimationFrame(releaseFrame);
      presses.current.clear();
    };
  }, [header]);
  return visible;
}
