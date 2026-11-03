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
    const pointerKey = (event: PointerEvent) => event.pointerType === "mouse" ? "mouse" : `pointer:${event.pointerId}`;
    const pointerDown = (event: PointerEvent) => { presses.current.add(pointerKey(event)); };
    const pointerUp = (event: PointerEvent) => release(pointerKey(event));
    const mouseDown = () => { presses.current.add("mouse"); };
    const mouseUp = (event: MouseEvent) => { if (!event.buttons) release("mouse"); };
    const keyDown = (event: KeyboardEvent) => {
      if (event.key === "Enter" || event.key === " ") presses.current.add(`key:${event.code}`);
    };
    const keyUp = (event: KeyboardEvent) => release(`key:${event.code}`);
    const reset = () => { presses.current.clear(); setVisible(desired.current); };
    root.addEventListener("pointerdown", pointerDown);
    root.addEventListener("mousedown", mouseDown, true);
    root.addEventListener("keydown", keyDown);
    root.addEventListener("dragend", reset);
    document.addEventListener("pointerup", pointerUp);
    document.addEventListener("pointercancel", pointerUp);
    document.addEventListener("mouseup", mouseUp, true);
    document.addEventListener("keyup", keyUp);
    window.addEventListener("blur", reset);
    return () => {
      root.removeEventListener("pointerdown", pointerDown);
      root.removeEventListener("mousedown", mouseDown, true);
      root.removeEventListener("keydown", keyDown);
      root.removeEventListener("dragend", reset);
      document.removeEventListener("pointerup", pointerUp);
      document.removeEventListener("pointercancel", pointerUp);
      document.removeEventListener("mouseup", mouseUp, true);
      document.removeEventListener("keyup", keyUp);
      window.removeEventListener("blur", reset);
      window.cancelAnimationFrame(releaseFrame);
      presses.current.clear();
    };
  }, [header]);
  return visible;
}
