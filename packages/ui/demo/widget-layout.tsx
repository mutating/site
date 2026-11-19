import { Activity, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { WidgetLayout } from "../src/react";
import "../src/styles/widget-layout.css";

function QueueStatus() {
  return "Queue paused";
}

function LoadingWidget() {
  return null;
}

function OptionalNestedLayout() {
  const [state, setState] = useState<"ready" | "empty" | "loading">("ready");
  useEffect(() => {
    const change = (event: Event) => setState((event as CustomEvent<typeof state>).detail);
    window.addEventListener("fixture-nested-widget", change);
    return () => window.removeEventListener("fixture-nested-widget", change);
  }, []);
  return <WidgetLayout data-testid="optional-nested" data-state={state}
    lead={state === "ready" ? <section>
      <button type="button" onClick={() => setState("empty")}>Remove nested widget</button>
    </section> : state === "loading" ? <LoadingWidget /> : null} />;
}

function FocusedWidget({ strategy }: { strategy: "auto" | "layout" }) {
  const input = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => {
    if (strategy === "layout") input.current?.focus();
  }, [strategy]);
  return <label>New widget draft <input ref={input} autoFocus={strategy === "auto"} /></label>;
}

function NewWidgetExample({ name, withLead, hidden }: { name: string; withLead?: boolean; hidden?: "attribute" | "inline" | "class" }) {
  const [strategy, setStrategy] = useState<"auto" | "layout" | null>(null);
  return <section aria-label={name}>
    <button type="button" onClick={() => setStrategy("auto")}>Mount autofocus widget</button>
    <button type="button" onClick={() => setStrategy("layout")}>Mount layout effect widget</button>
    <button type="button" onClick={() => setStrategy(null)}>Unmount focus widget</button>
    <WidgetLayout hidden={hidden === "attribute"} style={hidden === "inline" ? { display: "none" } : undefined}
      className={hidden === "class" ? "fixture-widget-hidden" : undefined}
      lead={withLead && <p style={{ minBlockSize: 1300 }}>Existing widget</p>}
      aside={strategy && <FocusedWidget strategy={strategy} />} />
  </section>;
}

function StyledNestedLayouts() {
  const [shown, setShown] = useState(false);
  return <section aria-label="Styled nested layouts">
    <button type="button" onClick={() => setShown(!shown)}>Toggle nested widgets</button>
    <WidgetLayout gap="18px" lead={<section data-testid="styled-lead">Primary widget</section>} aside={<>
      <WidgetLayout style={{ display: "grid", padding: 8, background: "lime" }}>
        {shown && <button type="button">Inline styled widget</button>}
      </WidgetLayout>
      <WidgetLayout className="fixture-widget-surface">
        {shown && <button type="button">Class styled widget</button>}
      </WidgetLayout>
    </>}>
      <section data-testid="styled-following">Following widget</section>
    </WidgetLayout>
  </section>;
}

function Example() {
  const [lead, setLead] = useState(true);
  const [aside, setAside] = useState(true);
  const [following, setFollowing] = useState(true);
  const [activity, setActivity] = useState(true);
  return <main data-testid="frame">
    <button type="button" onClick={() => setLead(!lead)}>Toggle lead</button>
    <button type="button" onClick={() => setAside(!aside)}>Toggle aside</button>
    <button type="button" onClick={() => setFollowing(!following)}>Toggle following</button>
    <button type="button" onClick={() => setActivity(!activity)}>Toggle activity</button>
    <WidgetLayout data-testid="layout" gap="18px" primaryWeight={0.5}
      lead={lead && <section data-testid="lead" style={{ minBlockSize: 62 }}>
          <label>Widget draft <input /></label>
        </section>}
      aside={aside && <section data-testid="aside" style={{ minBlockSize: 300 }}><button type="button">Side action</button></section>}>
      {following && <section data-testid="following"><button type="button">Following action</button></section>}
    </WidgetLayout>
    <WidgetLayout gap="20px" primaryWeight={2} aside={<section>Outer side</section>}
      lead={<WidgetLayout
        lead={<section data-testid="nested-first">{"unbroken".repeat(80)}</section>}
        aside={<section data-testid="nested-second">Nested widget</section>} />} />
    <WidgetLayout data-testid="text-layout" gap="18px"
      lead={<section data-testid="text-lead">Primary widget</section>}
      aside={<><QueueStatus /><section hidden data-testid="hidden-text-neighbor" style={{ display: "grid" }}>Hidden</section></>}>
      <section data-testid="text-following">Visible following widget</section>
      <section hidden data-testid="hidden-widget" style={{ display: "grid", minBlockSize: 500 }}>Hidden</section>
    </WidgetLayout>
    <WidgetLayout gap="18px" lead={<section data-testid="optional-lead">Primary widget</section>} aside={<OptionalNestedLayout />}>
      <section data-testid="optional-following">Following widget</section>
    </WidgetLayout>
    <WidgetLayout gap="18px" lead={<section data-testid="activity-lead">Primary widget</section>}
      aside={<Activity mode={activity ? "visible" : "hidden"}><section data-testid="activity-aside">
        <label>Activity draft <input /></label>
      </section></Activity>}>
      <section data-testid="activity-following">Following widget</section>
    </WidgetLayout>
    <NewWidgetExample name="Populated layout" withLead />
    <NewWidgetExample name="Empty layout" />
    <NewWidgetExample name="Natively hidden layout" hidden="attribute" />
    <NewWidgetExample name="Inline hidden layout" hidden="inline" />
    <NewWidgetExample name="CSS hidden layout" hidden="class" />
    <StyledNestedLayouts />
  </main>;
}

createRoot(document.getElementById("root")!).render(<Example />);
