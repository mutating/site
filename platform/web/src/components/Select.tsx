import {
  Children,
  isValidElement,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import { Icon } from "./UI";

type OptionInfo = {
  value: string;
  label: ReactNode;
  disabled: boolean;
};

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

function optionsOf(children: ReactNode): OptionInfo[] {
  const result: OptionInfo[] = [];
  const visit = (node: ReactNode) => {
    Children.forEach(node, (child) => {
      if (!isValidElement<{
        children?: ReactNode;
        value?: string | number;
        disabled?: boolean;
      }>(child)) return;
      if (child.type === "optgroup") {
        visit(child.props.children);
        return;
      }
      if (child.type !== "option") return;
      const text = textOf(child.props.children);
      result.push({
        value: child.props.value === undefined ? text : String(child.props.value),
        label: child.props.children,
        disabled: Boolean(child.props.disabled),
      });
    });
  };
  visit(children);
  return result;
}

/**
 * A native select keeps form, keyboard and assistive-technology behavior intact.
 * The visible face and listbox are a platform-styled pointer surface.
 */
export function Select({
  children,
  value,
  onChange,
  disabled = false,
  multiple = false,
  size,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  const rootRef = useRef<HTMLSpanElement>(null);
  const selectRef = useRef<HTMLSelectElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const openedAt = useRef(0);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{
    top?: number;
    bottom?: number;
    left?: number;
    right?: number;
    width: number;
    maxHeight: number;
  } | null>(null);
  const options = useMemo(() => optionsOf(children), [children]);
  const selected = options.find((option) => option.value === String(value));
  const scrolledOpen = useRef(false);

  const positionPanel = useCallback((anchor?: DOMRect) => {
    const root = rootRef.current;
    const panel = panelRef.current;
    if (!root || !panel) return;
    const rect = anchor ?? root.getBoundingClientRect();
    const width = Math.min(Math.max(rect.width, 240), 320, window.innerWidth - 24);
    // The first layout uses shrink-to-fit width; apply the final width before
    // measuring wrapping-dependent height and flip position.
    panel.style.width = `${width}px`;
    const style = getComputedStyle(panel);
    const borderY =
      parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const viewportHeight = window.innerHeight;
    const cssCap = Math.min(300, viewportHeight * 0.45);
    const contentHeight = Math.min(panel.scrollHeight + borderY, cssCap);
    const anchorTop = Math.max(0, Math.min(rect.top, viewportHeight));
    const anchorBottom = Math.max(0, Math.min(rect.bottom, viewportHeight));
    const spaceBelow = viewportHeight - anchorBottom - 12;
    const spaceAbove = anchorTop - 12;
    const above = spaceBelow < contentHeight && spaceAbove > spaceBelow;
    const maxHeight = Math.max(
      0,
      Math.min(contentHeight, above ? spaceAbove : spaceBelow),
    );
    const alignRight = rect.left + 240 > window.innerWidth - 12;
    setPosition({
      top: above ? undefined : anchorBottom + 6,
      bottom: above ? viewportHeight - anchorTop + 6 : undefined,
      left: alignRight ? undefined : rect.left,
      right: alignRight ? window.innerWidth - rect.right : undefined,
      width,
      maxHeight,
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      scrolledOpen.current = false;
      return;
    }
    const root = rootRef.current;
    if (!root) return;
    let anchor = root.getBoundingClientRect();
    positionPanel(anchor);
    const followAnchor = () => {
      const rect = root.getBoundingClientRect();
      if (rect.left !== anchor.left || rect.right !== anchor.right || rect.top !== anchor.top || rect.bottom !== anchor.bottom) {
        anchor = rect;
        positionPanel(rect);
      }
      frame = window.requestAnimationFrame(followAnchor);
    };
    let frame = window.requestAnimationFrame(followAnchor);
    return () => window.cancelAnimationFrame(frame);
  }, [open, options.length, multiple, size, positionPanel]);

  useLayoutEffect(() => {
    if (!open || !position || scrolledOpen.current) return;
    scrolledOpen.current = true;
    panelRef.current
      ?.querySelector<HTMLElement>('[data-selected="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [open, position, options.length]);

  useEffect(() => {
    setOpen(false);
  }, [value, disabled]);

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      selectRef.current?.focus({ preventScroll: true });
    };
    const closeOnViewportChange = (event: Event) => {
      if (event.target instanceof Node && panelRef.current?.contains(event.target)) return;
      if (performance.now() - openedAt.current < 200) {
        positionPanel();
        return;
      }
      setOpen(false);
    };
    const closeOnNavigation = () => setOpen(false);
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("scroll", closeOnViewportChange, true);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("popstate", closeOnNavigation);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("scroll", closeOnViewportChange, true);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("popstate", closeOnNavigation);
    };
  }, [open, positionPanel]);

  if (multiple || size !== undefined) {
    return (
      <select
        ref={selectRef}
        value={value}
        onChange={onChange}
        disabled={disabled}
        multiple={multiple}
        size={size}
        {...props}
      >
        {children}
      </select>
    );
  }

  const choose = (option: OptionInfo) => {
    const select = selectRef.current;
    if (!select || select.disabled || option.disabled) return;
    if (option.value === String(value)) {
      setOpen(false);
      select.focus({ preventScroll: true });
      return;
    }
    select.value = option.value;
    onChange?.({ target: select, currentTarget: select } as ChangeEvent<HTMLSelectElement>);
    setOpen(false);
    select.focus({ preventScroll: true });
  };

  return (
    <span
      ref={rootRef}
      className="tmc-select"
      data-open={open}
      data-disabled={disabled}
    >
      <select
        ref={selectRef}
        value={value}
        onChange={(event) => {
          onChange?.(event);
          setOpen(false);
        }}
        disabled={disabled}
        onKeyDown={(event) => {
          if (!open) return;
          if (event.key === "Escape") {
            event.preventDefault();
            setOpen(false);
            return;
          }
          setOpen(false);
        }}
        {...props}
      >
        {children}
      </select>
      <span
        className="tmc-select__face"
        aria-hidden="true"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          if (disabled || selectRef.current?.matches(":disabled")) return;
          selectRef.current?.focus({ preventScroll: true });
          openedAt.current = performance.now();
          setOpen((current) => !current);
        }}
      >
        <span className="tmc-select__value">{selected?.label ?? ""}</span>
        <svg
          className="tmc-select__chevron"
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          aria-hidden="true"
        >
          <path
            d="m2.5 4.5 3.5 3.5 3.5-3.5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      {open && (
        <div
          ref={panelRef}
          className="tmc-select__panel"
          style={{
            top: position?.top,
            bottom: position?.bottom,
            left: position?.left,
            right: position?.right,
            width: position?.width,
            maxHeight: position?.maxHeight,
            visibility: position ? "visible" : "hidden",
          }}
          aria-hidden="true"
          onMouseDown={(event) => {
            const panel = event.currentTarget;
            const style = getComputedStyle(panel);
            const borderLeft = parseFloat(style.borderLeftWidth);
            const borderRight = parseFloat(style.borderRightWidth);
            const scrollbar =
              panel.offsetWidth - panel.clientWidth - borderLeft - borderRight;
            const scrollbarLeft =
              panel.getBoundingClientRect().left + borderLeft + panel.clientWidth;
            if (
              event.target === panel &&
              scrollbar > 0 &&
              event.clientX >= scrollbarLeft - 1
            )
              return;
            event.preventDefault();
          }}
          onPointerUp={() => selectRef.current?.focus({ preventScroll: true })}
        >
          {options.map((option, index) => (
            <div
              key={`${option.value}-${index}`}
              className="tmc-select__option"
              data-selected={option.value === String(value)}
              data-disabled={option.disabled}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(option)}
            >
              <span className="tmc-select__option-label">{option.label}</span>
              {option.value === String(value) && (
                <span className="tmc-select__check">
                  <Icon name="check" size={14} />
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </span>
  );
}
