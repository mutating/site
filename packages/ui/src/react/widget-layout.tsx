import {
  Children, Fragment, isValidElement, useLayoutEffect, useRef, useState,
  type ComponentPropsWithRef, type CSSProperties, type ReactNode,
} from "react";

export interface WidgetLayoutProps extends ComponentPropsWithRef<"div"> {
  gap?: string;
  lead?: ReactNode;
  aside?: ReactNode;
  primaryWeight?: number;
}

function groupHasContent(content: ReactNode): boolean {
  return Children.toArray(content).some((widget) => {
    if (typeof widget === "string") return Boolean(widget.trim());
    if (!isValidElement<{ children?: ReactNode; hidden?: boolean | "until-found"; lead?: ReactNode; aside?: ReactNode; style?: CSSProperties }>(widget)) return true;
    if (widget.type === Fragment) return groupHasContent(widget.props.children);
    const { children, lead, aside, hidden, style } = widget.props;
    const visible = style?.display !== "none" && (!hidden || hidden === "until-found");
    if (widget.type === WidgetLayout) {
      return visible && [lead, aside, children].some(groupHasContent);
    }
    return typeof widget.type !== "string" || visible;
  });
}

function isRenderedWidget(widget: ChildNode) {
  if (widget.nodeType === Node.TEXT_NODE) return Boolean(widget.textContent?.trim());
  if (widget.nodeType !== Node.ELEMENT_NODE) return false;
  const element = widget as HTMLElement | SVGElement;
  const hidden = element.getAttribute("hidden");
  return element.style?.display !== "none" && !element.hasAttribute("data-tmc-widget-empty") &&
    (hidden === null || hidden.toLowerCase() === "until-found");
}

export function WidgetLayout({ gap, lead, aside, children, primaryWeight, className, style, ...props }: WidgetLayoutProps) {
  const groups = [["lead", lead], ["aside", aside], ["following", children]] as const;
  // Keep the initial HTML attributes stable. The observer owns visibility after mounting.
  const [initialPresence] = useState(() => groups.map(([, content]) => groupHasContent(content)));
  const gridRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const grid = gridRef.current!;
    const layout = grid.parentElement!;
    const update = () => {
      let populated = false;
      for (const group of grid.children) {
        const present = Array.from(group.childNodes).some(isRenderedWidget);
        group.toggleAttribute("data-tmc-widget-empty", !present);
        populated ||= present;
      }
      layout.toggleAttribute("data-tmc-widget-empty", !populated);
    };
    // Nested layouts and widgets may change visibility without rendering their parent.
    const observer = new MutationObserver((changes) => {
      if (changes.some(({ type, target }) => type === "childList" ? target.parentNode === grid : target.parentNode?.parentNode === grid)) {
        update();
      }
    });
    update();
    observer.observe(grid, {
      childList: true, subtree: true, characterData: true, attributes: true,
      attributeFilter: ["hidden", "style", "data-tmc-widget-empty"],
    });
    return () => observer.disconnect();
  }, []);

  const variables = {
    ...style,
    ...(gap === undefined ? {} : { "--tmc-widget-layout-gap": gap }),
    ...(primaryWeight !== undefined && Number.isFinite(primaryWeight) && primaryWeight > 0
      ? { "--tmc-widget-layout-primary-track": `${primaryWeight}fr` } : {}),
  } as CSSProperties;
  return <div {...props} className={className ? `tmc-widget-layout ${className}` : "tmc-widget-layout"} style={variables}
    data-tmc-widget-empty={initialPresence.some(Boolean) ? undefined : ""}>
    <div ref={gridRef} className="tmc-widget-layout__grid">
      {groups.map(([area, content], index) => <div key={area}
        className={`tmc-widget-layout__group tmc-widget-layout__${area}`}
        data-tmc-widget-empty={initialPresence[index] ? undefined : ""}>{content}</div>)}
    </div>
  </div>;
}
