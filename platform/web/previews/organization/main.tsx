import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Brand } from "@mutating/ui/react";
import { Icon } from "../../src/components/UI";
import { WorkspaceDialog } from "./dialogs";
import { OrganizationDesign } from "./layouts";
import { bases, designs, designsByRound, initialWorkspace, rounds, secondRound, thirdRound, type Design, type Round, type Theme, type Workspace } from "./model";
import { WorkspaceContext, type DialogState, type Section } from "./parts";
import "@mutating/ui/styles.css";
import "./showcase.css";
import "./designs.css";
import "./round-two.css";
import "./round-three.css";

const roundHref = (round: Round) => `#round-${round}`;
const roundFor = (design: Design): Round => "family" in design ? 3 : "base" in design ? 2 : 1;
const initialSection = (design: Design): Section => design.key === "atlas-tabs" || design.key === "mural-tabs" ? "machines" : "team";
const latestRound = rounds[rounds.length - 1];
const demoContext = { workspace: initialWorkspace, open: () => {}, section: "team" as const, setSection: () => {} };
const demoMachineContext = { ...demoContext, section: "machines" as const };

type Family = { source: Design["number"]; title?: string; sourceLabel?: string; entries: readonly Design[] };

const galleryFamilies: Partial<Record<Round, readonly Family[]>> = {
  2: bases.map((source) => ({ source, entries: secondRound.filter((design) => design.base === source) })),
  3: [
    { source: "26", entries: thirdRound.filter((design) => design.family === "light") },
    { source: "32", entries: thirdRound.filter((design) => design.family === "mural") },
    { source: "29", title: "Яркий розовый", sourceLabel: "Цвет из №29", entries: thirdRound.filter((design) => design.family === "accent") },
  ],
};

const galleryDescriptions: Record<Round, string> = {
  1: "20 вариантов на одних демоданных. Откройте любой, чтобы посмотреть его в полном размере и попробовать действия.",
  2: "20 вариантов на основе «Атласа», «Розового блока» и «Панорамы». Откройте любой, чтобы попробовать его на демоданных.",
  3: "20 новых вариантов на основе «Светлого атласа» и «Панно». В отдельных версиях использован яркий розовый из №29.",
};

function Topbar({ round }: { round: Round }) {
  return <header className="showcase-topbar">
    <Brand href={roundHref(round)} animation={{ expandWordmark: false, blinkIntervalMs: 0 }} />
    <span className="showcase-product">Mutating Compute</span>
    <a className="topbar-index" href={roundHref(round)}><Icon name="grid" size={17} />Все варианты</a>
  </header>;
}

function VariantGrid({ entries }: { entries: readonly Design[] }) {
  return <div className="gallery-grid">
    {entries.map((design) => <article className="variant-card" key={design.number}>
      <div className="variant-cover" aria-hidden="true" inert>
        <div className="miniature design-frame">
          <WorkspaceContext.Provider value={initialSection(design) === "machines" ? demoMachineContext : demoContext}>
            <OrganizationDesign design={design} theme={design.theme} />
          </WorkspaceContext.Provider>
        </div>
      </div>
      <div className="variant-caption">
        <span className="variant-number">{design.number}</span>
        <div><a href={`#${design.number}`} aria-label={`${design.number} · ${design.name}`}>{design.name}</a><p>{design.description}</p></div>
        <Icon name="arrow" size={19} />
      </div>
    </article>)}
  </div>;
}

function Gallery({ round }: { round: Round }) {
  const gallery = useRef<HTMLElement>(null);
  const families = galleryFamilies[round];
  useLayoutEffect(() => {
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        (entry.target as HTMLElement).style.setProperty("--thumbnail-scale", String(entry.contentRect.width / 1060));
      }
    });
    gallery.current?.querySelectorAll(".variant-cover").forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, [round]);

  return <main className="gallery-page" data-round={round} ref={gallery}>
    <header className="gallery-intro">
      <div className="gallery-topline">
        <div className="gallery-kicker"><Icon name="building" size={16} /> Организации</div>
        <nav className="round-switcher" aria-label="Раунды дизайна">
          {rounds.map((entry) => <a key={entry} href={roundHref(entry)} aria-current={round === entry ? "page" : undefined}>Раунд {entry}</a>)}
        </nav>
      </div>
      <h1 tabIndex={-1}>Страница <br /><em>организации<span>.</span></em></h1>
      <p>{galleryDescriptions[round]}</p>
    </header>
    {families ? families.map(({ source, title, sourceLabel, entries }) => <section className="gallery-family" key={source} aria-labelledby={`family-${source}`}>
      <header>
        <h2 id={`family-${source}`}>{title ?? designs.find((entry) => entry.number === source)?.name}</h2>
        <a href={`#${source}`}>{sourceLabel ?? `Исходный №${source}`}<Icon name="arrow" size={15} /></a>
      </header>
      <VariantGrid entries={entries} />
    </section>) : <VariantGrid entries={designsByRound[round]} />}
  </main>;
}

function PreviewBar({ design, theme, toggleTheme }: { design: Design; theme: Theme; toggleTheme: () => void }) {
  const round = roundFor(design);
  const entries: readonly Design[] = designsByRound[round];
  const index = entries.indexOf(design);
  const previous = entries[(index + entries.length - 1) % entries.length];
  const next = entries[(index + 1) % entries.length];
  return <div className="preview-bar">
    <a href={roundHref(round)} className="preview-back"><Icon name="arrow" size={17} />К галерее</a>
    <div className="variant-picker">
      <a href={`#${previous.number}`} aria-label={`Предыдущий вариант: ${previous.name}`}><Icon name="arrow" size={17} /></a>
      <label><select aria-label="Выбрать вариант" value={design.number} onChange={(event) => { window.location.hash = event.currentTarget.value; }}>
        {rounds.map((choiceRound) => <optgroup key={choiceRound} label={`Раунд ${choiceRound}`}>
          {designsByRound[choiceRound].map((choice) => <option key={choice.number} value={choice.number}>{choice.number} · {choice.name}</option>)}
        </optgroup>)}
      </select><Icon name="chevron-down" size={14} /></label>
      <a href={`#${next.number}`} aria-label={`Следующий вариант: ${next.name}`}><Icon name="arrow" size={17} /></a>
    </div>
    <button type="button" className="preview-theme" onClick={toggleTheme} aria-pressed={theme === "dark"}><span aria-hidden="true" /> <span>Тёмная тема</span></button>
  </div>;
}

const sideItems = [["grid", "Обзор"], ["compute", "Compute"], ["play", "Actions"], ["cloud", "Облако"], ["users", "Организации"]] as const;

function DemoWorkspace({ design, theme }: { design: Design; theme: Theme }) {
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const [section, setSection] = useState<Section>(() => initialSection(design));
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [notice, setNotice] = useState<{ message: string } | null>(null);
  const dialogOpener = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  function updateWorkspace(update: (current: Workspace) => Workspace, message: string) {
    setWorkspace(update);
    setDialog(null);
    setNotice({ message });
  }

  return <div className="product-preview" data-theme={theme} onClickCapture={(event) => {
    if (!dialog && event.target instanceof Element) dialogOpener.current = event.target.closest("button");
  }}>
    <aside className="preview-sidebar" aria-hidden="true"><span className="overline">Личный кабинет</span>
      <ul>{sideItems.map(([icon, label]) => <li className={icon === "users" ? "is-current" : undefined} key={icon}><Icon name={icon} size={18} />{label}</li>)}</ul>
    </aside>
    <div className="live-stage">
      <div className="stage-crumb">
        <div className="stage-crumb-start"><span><Icon name="users" size={15} /> Организации</span>{"base" in design && <a href={`#${design.base}`}>Основа №{design.base}</a>}</div>
        <span>Демоданные</span>
      </div>
      <WorkspaceContext.Provider value={{ workspace, open: setDialog, section, setSection }}>
        <div className="design-frame live-design"><OrganizationDesign design={design} theme={theme} /></div>
        {notice && <p className="preview-notice" role="status"><Icon name="check" size={19} />{notice.message}</p>}
        {dialog && <WorkspaceDialog state={dialog} theme={theme} opener={dialogOpener.current} close={() => setDialog(null)} update={updateWorkspace} />}
      </WorkspaceContext.Provider>
    </div>
  </div>;
}

function Preview({ design }: { design: Design }) {
  const [override, setOverride] = useState<{ number: Design["number"]; theme: Theme } | null>(null);
  const theme = override?.number === design.number ? override.theme : design.theme;
  return <main className="preview-page">
    <PreviewBar design={design} theme={theme} toggleTheme={() => setOverride({ number: design.number, theme: theme === "dark" ? "light" : "dark" })} />
    <DemoWorkspace key={design.number} design={design} theme={theme} />
  </main>;
}

function App() {
  const [hash, setHash] = useState(() => window.location.hash);
  const design = designs.find((entry) => `#${entry.number}` === hash);
  const round = design ? roundFor(design) : rounds.find((entry) => hash === roundHref(entry)) ?? latestRound;
  const galleryViews = useRef<Partial<Record<Round, { scroll: number; selection?: string }>>>({});
  const lastView = useRef({ design, round });

  useEffect(() => {
    const originalRestoration = history.scrollRestoration;
    history.scrollRestoration = "manual";
    const navigate = () => {
      const before = lastView.current;
      const nextHash = window.location.hash;
      if (!before.design) {
        galleryViews.current[before.round] = {
          scroll: window.scrollY,
          selection: designs.find((entry) => `#${entry.number}` === nextHash)?.number,
        };
      }
      setHash(nextHash);
    };
    window.addEventListener("hashchange", navigate);
    return () => {
      window.removeEventListener("hashchange", navigate);
      history.scrollRestoration = originalRestoration;
    };
  }, []);

  useLayoutEffect(() => {
    const previous = lastView.current;
    const gallery = galleryViews.current[round];
    if (!previous.design && design) document.querySelector<HTMLElement>(".live-design h1")?.focus({ preventScroll: true });
    if (previous.design && !design) {
      const selection = gallery?.selection;
      const link = selection && document.querySelector<HTMLElement>(`.gallery-page a[href='#${selection}']`);
      (link || document.querySelector<HTMLElement>(".gallery-intro h1"))?.focus({ preventScroll: true });
    }
    window.scrollTo(0, design ? 0 : gallery?.scroll ?? 0);
    document.title = design ? `${design.number} · ${design.name} — Страница организации` : "Страница организации · The Mutating Company";
    lastView.current = { design, round };
  }, [design, round]);

  return <><Topbar round={round} />{design ? <Preview design={design} /> : <Gallery round={round} />}</>;
}

createRoot(document.getElementById("root")!).render(<App />);
