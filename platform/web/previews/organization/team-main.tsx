import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { Brand } from "@mutating/ui/react";
import organizationArtwork from "../../src/assets/organization-atlas.webp";
import { Icon } from "../../src/components/UI";
import { I18nProvider } from "../../src/lib/i18n";
import { stepPath } from "../../src/lib/chart";
import { WorkspaceDialog } from "./dialogs";
import { machines, stats, type Theme, type Workspace } from "./model";
import { WorkspaceContext, useWorkspace, type DialogState } from "./parts";
import { MemberDirectory } from "./team-directory";
import { demoSizes, directoryWorkspaces, memberCount, memberDirectoryHref, memberLinkDesigns, memberNumber, memberPreviewLimit, type DemoSize } from "./team-directory-model";
import { teamDesigns, teamFamilies, teamWorkspace, type TeamDesign } from "./team-model";
import { TeamWidget, type MemberSearchState, type TeamPane } from "./team-widget";
import "@mutating/ui/styles.css";
import "./showcase.css";
import "./designs.css";
import "../../src/styles/brand.css";
import "../../src/styles/app.css";
import "../../src/styles/organization.css";
import "./team.css";
import "./team-directory.css";

const sidebar = [["grid", "Обзор"], ["compute", "Compute"], ["play", "Actions"], ["cloud", "Облако"], ["users", "Организации"]] as const;
const noAction = () => {};

function OrganizationCover() {
  const { workspace, open } = useWorkspace();
  const breakAt = workspace.name.indexOf(" ");
  return <header className="organization-hero">
    <div className="organization-hero-art" aria-hidden="true"><img src={organizationArtwork} alt="" /></div>
    <div className="organization-hero-copy">
      <div className="organization-hero-context"><p className="eyebrow">Организация</p></div>
      <h1 tabIndex={-1} aria-label={`${workspace.name}.`}>
        {breakAt > 0 ? <>{workspace.name.slice(0, breakAt)}{" "}<br /><span>{workspace.name.slice(breakAt + 1)}</span></> : workspace.name}
        <span className="hot" aria-hidden="true">.</span>
      </h1>
      <p className="lede">Общее рабочее пространство</p>
      <button type="button" className="button primary compact organization-settings-trigger" onClick={() => open({ kind: "settings" })}>
        <Icon name="building" size={16} />Настройки организации
      </button>
    </div>
  </header>;
}

function OrganizationOverview() {
  const { workspace } = useWorkspace();
  const metrics = [
    { icon: "computer", label: "Компьютеры", value: machines.length, detail: `${stats.online} доступно` },
    { icon: "cpu", label: "Свободно CPU", value: stats.freeCpu, detail: `из ${stats.totalCpu} ядер` },
    { icon: "memory", label: "Свободно памяти", value: stats.freeMemory, detail: `из ${stats.totalMemory} GiB` },
    { icon: "users", label: "Участники", value: memberNumber(workspace.members.length) },
  ];
  return <section className="organization-widget organization-overview" aria-label="Сводка организации">
    <dl className="organization-overview-metrics">
      {metrics.map(({ icon, label, value, detail }) => <div key={icon}>
        <dt><Icon name={icon} size={17} />{label}</dt><dd>{value}</dd>
        {detail && <dd className="organization-metric-detail">{detail}</dd>}
      </div>)}
    </dl>
  </section>;
}

function OrganizationComputers() {
  const { open } = useWorkspace();
  return <section className="organization-widget organization-computers" aria-label="Компьютеры">
    <header className="organization-widget-header">
      <h2>Компьютеры <span className="organization-widget-count" aria-hidden="true">{machines.length}</span></h2>
      <button type="button" className="text-link team-context-link" onClick={() => open({ kind: "computers" })}>Список компьютеров <Icon name="arrow" size={15} /></button>
    </header>
    <ul className="organization-computer-cards">
      {machines.map((machine) => <li key={machine.id}>
        <div className="organization-computer-card">
          <div className="organization-computer-top">
            <span className="organization-computer-icon"><Icon name="computer" size={20} /></span>
            <span className={`organization-computer-status ${machine.online ? "online" : "offline"}`}><i aria-hidden="true" />{machine.online ? "В сети" : "Не в сети"}</span>
          </div>
          <div className="organization-computer-identity"><strong>{machine.name}</strong><small>{machine.platform}</small></div>
          <div className="organization-computer-capacity"><span>Свободно CPU</span><strong>{machine.online ? machine.cpu : "—"} / {machine.cores}</strong></div>
        </div>
      </li>)}
    </ul>
  </section>;
}

const start = Date.UTC(2026, 10, 13);
const day = 86_400_000;
const resourceCharts = [
  { kind: "cpu", title: "CPU организации", unit: "ядер", total: [24, 24, 32, 32, 32, stats.totalCpu, stats.totalCpu], free: [16, 12, 16, 21, 17, 19, stats.freeCpu] },
  { kind: "memory", title: "Память организации", unit: "GiB", total: [48, 48, 72, 72, 72, stats.totalMemory, stats.totalMemory], free: [27, 24, 36, 46, 40, 44, stats.freeMemory] },
] as const;

function OrganizationHistory() {
  return <div className="organization-history-grid">
    {resourceCharts.map(({ kind, title, unit, total, free }) => {
      const points = total.map((value, index) => ({ at: new Date(start + index * day).toISOString(), total: value, free: free[index] }));
      const maximum = Math.max(...total);
      return <section className="organization-widget organization-history" key={kind} aria-label={title}>
        <header className="organization-widget-header"><h2><Icon name={kind} size={16} />{title}</h2><span className="organization-widget-count">7 дней</span></header>
        <figure className={`organization-resource-chart ${kind}`}>
          <figcaption><span><i className="total" />Всего <strong>{total.at(-1)} {unit}</strong></span><span><i className="free" />Свободно <strong>{free.at(-1)} {unit}</strong></span></figcaption>
          <svg viewBox="0 0 300 120" role="img" aria-label={`${title} за 7 дней`}>
            {[0, .5, 1].map((fraction) => <g key={fraction}>
              <line className="chart-grid" x1="24" x2="288" y1={12 + (1 - fraction) * 88} y2={12 + (1 - fraction) * 88} />
              <text className="tick" x="18" y={15 + (1 - fraction) * 88} textAnchor="end">{fraction * maximum}</text>
            </g>)}
            <g transform="translate(24 12)">
              <path className="total" d={stepPath(points, "total", start, start + 6 * day, maximum, 264, 88)} />
              <path className="free" d={stepPath(points, "free", start, start + 6 * day, maximum, 264, 88)} />
            </g>
            <text className="tick" x="24" y="116">13 нояб.</text><text className="tick" x="288" y="116" textAnchor="end">19 нояб.</text>
          </svg>
        </figure>
      </section>;
    })}
  </div>;
}

function Toolbar({ design, gallery, links, theme, changeTheme, jumpToWidget, reset }: {
  design: TeamDesign | null;
  gallery: boolean;
  links: boolean;
  theme: Theme;
  changeTheme: () => void;
  jumpToWidget: () => void;
  reset?: () => void;
}) {
  const choices = links ? memberLinkDesigns : teamDesigns;
  const index = design ? choices.indexOf(design) : -1;
  const before = choices.at(index - 1)!;
  const after = choices[(index + 1) % choices.length];
  const galleryHref = links ? "#links" : "#all";
  return <>
    <Brand href={galleryHref} animation={{ expandWordmark: false, blinkIntervalMs: 0 }} />
    <a href={galleryHref} className="team-all-link" aria-current={gallery ? "page" : undefined}><Icon name="grid" size={16} />Все {choices.length}</a>
    {design && <nav className="team-design-picker" aria-label="Переключение вариантов">
      <a href={`#${before.number}`} aria-label={`Предыдущий вариант ${before.number}`}><Icon name="arrow" size={16} /></a>
      <label><select aria-label="Вариант виджета" value={design.number} onChange={(event) => { window.location.hash = event.currentTarget.value; }}>
        {links ? choices.map((item) => <option key={item.number} value={item.number}>{item.number} · {item.name}</option>) : teamFamilies.map(({ name, first, entries }) => <optgroup key={first} label={name}>
          {entries.map((item) => <option key={item.number} value={item.number}>{item.number} · {item.name}</option>)}
        </optgroup>)}
      </select><Icon name="chevron-down" size={14} /></label>
      <a href={`#${after.number}`} aria-label={`Следующий вариант ${after.number}`}><Icon name="arrow" size={16} /></a>
    </nav>}
    <div className="team-toolbar-actions">
      {reset && <button type="button" className="team-toolbar-button" aria-label="Сбросить демоданные" onClick={reset}><Icon name="refresh" size={16} /></button>}
      {design && <button type="button" className="team-toolbar-button team-jump" onClick={jumpToWidget}><Icon name="users" size={16} />К виджету</button>}
      <button type="button" className="team-toolbar-button team-theme" aria-label="Тёмная тема" aria-pressed={theme === "dark"} onClick={changeTheme}>
        <span className="team-theme-icon" aria-hidden="true" /><span>Тёмная тема</span>
      </button>
    </div>
  </>;
}

type DemoSizeControl = { value: DemoSize; setValue: (value: DemoSize) => void };

function DemoSizeSelect({ value, setValue }: DemoSizeControl) {
  return <label className="team-demo-size"><span>В демокоманде</span>
    <select value={value} aria-label="Размер демокоманды" onChange={(event) => setValue(Number(event.currentTarget.value) as DemoSize)}>
      {demoSizes.map((size) => <option key={size} value={size}>{memberCount(size)}</option>)}
    </select>
  </label>;
}

function TeamGallery({ pane, select, sizeControl }: { pane: TeamPane; select: (pane: TeamPane) => void; sizeControl?: DemoSizeControl }) {
  const families = sizeControl ? [{ name: "", first: 81, entries: memberLinkDesigns }] : teamFamilies;
  return <main className={`team-gallery${sizeControl ? " team-link-gallery" : ""}`}>
    <header className="team-gallery-intro">
      <div>{sizeControl ? <>
        <h1 tabIndex={-1}>Переход <em>к участникам<span>.</span></em></h1>
        <p className="lede">Десять вариаций №61. Если в команде больше пяти человек, виджет показывает первые пять и ссылку на полный список.</p>
        <a className="team-gallery-directory-link" href="#all"><Icon name="grid" size={16} />К вариантам «Команды»</a>
      </> : <>
        <h1 tabIndex={-1}>20 вариантов <em>«Команды»<span>.</span></em></h1><p className="lede">Откройте любой вариант на странице «Северной лаборатории».</p>
        <a className="team-gallery-directory-link" href="#links">10 способов открыть список участников <Icon name="arrow" size={16} /></a>
      </>}</div>
      {sizeControl ? <DemoSizeSelect {...sizeControl} /> : <div className="team-gallery-switch" role="group" aria-label="Раздел для сравнения">
        <button type="button" aria-pressed={pane === "members"} onClick={() => select("members")}>Участники</button>
        <button type="button" aria-pressed={pane === "invitations"} onClick={() => select("invitations")}>Приглашения</button>
      </div>}
    </header>
    {families.map(({ name, first, entries }) => <section className="team-gallery-family" aria-labelledby={name ? `family-team-${first}` : undefined} key={first}>
      {name && <h2 id={`family-team-${first}`}>{name}</h2>}
      <div className="team-gallery-grid">
        {entries.map((design) => <article className="team-gallery-card" key={design.number}>
          <div className="team-gallery-sample" inert aria-hidden="true">
            <TeamWidget key={pane} design={design} pane={sizeControl ? "members" : pane} select={noAction} />
          </div>
          <div className="team-gallery-caption">
            <span className="team-gallery-number">{design.number}</span>
            <div><a href={`#${design.number}`} aria-label={`${design.number} · ${design.name}`}>{design.name}</a><p>{design.description}</p></div>
            <Icon name="arrow" size={17} />
          </div>
        </article>)}
      </div>
    </section>)}
  </main>;
}

function PreviewShell({ children }: { children: ReactNode }) {
  return <div className="app-shell team-preview-shell">
    <aside className="sidebar" aria-label="Разделы платформы">
      <p className="sidebar-label eyebrow">Личный кабинет</p>
      <div className="team-sidebar-items">
        {sidebar.map(([icon, label]) => <span className={`nav-link${icon === "users" ? " active" : ""}`} key={icon}><Icon name={icon} size={18} />{label}</span>)}
      </div>
    </aside>
    <main className="main-content">{children}</main>
  </div>;
}

function OrganizationPage({ design, pane, select, sizeControl, memberSearch }: {
  design: TeamDesign; pane: TeamPane; select: (pane: TeamPane) => void; sizeControl?: DemoSizeControl; memberSearch?: MemberSearchState;
}) {
  return <PreviewShell>
    <div className="team-page-crumb"><span><Icon name="users" size={15} />Организации</span>
      {sizeControl ? <DemoSizeSelect {...sizeControl} /> : <span>Демоданные</span>}
    </div>
    <div className="organization-detail">
      <OrganizationCover />
      <OrganizationOverview />
      <div className="organization-widgets with-history">
        <OrganizationComputers />
        <div id="team-widget" className="organization-people team-widget-position">
          <TeamWidget key={design.number} design={design} pane={pane} select={select} memberSearch={memberSearch} />
        </div>
        <OrganizationHistory />
      </div>
    </div>
  </PreviewShell>;
}

type MemberDirectoryOrigin = { scroll: number; source: number };
type View = { kind: "gallery"; links: boolean }
  | { kind: "organization"; design: TeamDesign }
  | { kind: "members"; design: TeamDesign; search: string; origin?: MemberDirectoryOrigin };

function readView(): View {
  const [path, query] = window.location.hash.slice(1).split("?", 2);
  if (path === "all" || path === "links") return { kind: "gallery", links: path === "links" };
  const directoryDesign = memberLinkDesigns.find(({ number }) => path === `members-${number}`);
  if (directoryDesign) return { kind: "members", design: directoryDesign, search: new URLSearchParams(query).get("q") ?? "", origin: history.state?.memberDirectoryOrigin };
  const design = teamDesigns.find(({ number }) => path === String(number))
    ?? memberLinkDesigns.find(({ number }) => path === String(number))
    ?? teamDesigns[0];
  return { kind: "organization", design };
}

function TeamPreview() {
  const [view, setView] = useState(readView);
  const [theme, setTheme] = useState<Theme>("light");
  const [pane, setPane] = useState<TeamPane>("members");
  const [standardWorkspace, setStandardWorkspace] = useState(teamWorkspace);
  const [directoryWorkspace, setDirectoryWorkspace] = useState(directoryWorkspaces[2000]);
  const [demoSize, setDemoSize] = useState<DemoSize>(2000);
  const [widgetSearch, setWidgetSearch] = useState(view.kind === "members" ? view.search : "");
  const links = view.kind === "gallery" ? view.links : !!view.design.memberLink;
  const workspace = links ? directoryWorkspace : standardWorkspace;
  const setWorkspace = links ? setDirectoryWorkspace : setStandardWorkspace;
  const initial = links ? directoryWorkspaces[demoSize] : teamWorkspace;
  const sizeControl = links ? { value: demoSize, setValue: (value: DemoSize) => {
    setDemoSize(value);
    setDirectoryWorkspace(directoryWorkspaces[value]);
    setWidgetSearch("");
  } } : undefined;
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [notice, setNotice] = useState("");
  const toolbar = useRef<HTMLElement>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const previous = useRef(view);
  const galleryViews = useRef<Partial<Record<"standard" | "links", { scroll: number; source: number }>>>({});

  function revealControl(control: HTMLElement) {
    const { top, bottom, left, right } = control.getBoundingClientRect();
    const bar = toolbar.current;
    const covered = !!bar && !bar.contains(control) && top < bar.getBoundingClientRect().bottom;
    const notice = document.querySelector(".team-showcase > .preview-notice")?.getBoundingClientRect();
    const behindNotice = !!notice && left < notice.right && right > notice.left && top < notice.bottom && bottom > notice.top;
    if (behindNotice) control.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
    else if (covered || bottom > innerHeight + 1) control.scrollIntoView({ block: covered ? "start" : "nearest", inline: "nearest", behavior: "instant" });
  }

  function focusControl(control: HTMLElement | null) {
    if (!control) return;
    control.focus({ preventScroll: true });
    revealControl(control);
  }

  useEffect(() => {
    const saved = history.scrollRestoration;
    history.scrollRestoration = "manual";
    const navigate = () => {
      const before = previous.current;
      const next = readView();
      if (before.kind === "gallery" && next.kind === "organization") {
        galleryViews.current[before.links ? "links" : "standard"] = { scroll: window.scrollY, source: next.design.number };
      }
      if (next.kind === "organization" && next.design.memberLink && (before.kind !== "organization" || !before.design.memberLink)) setPane("members");
      if (next.kind === "members") {
        if (!next.origin && before.kind === "organization" && before.design.number === next.design.number) {
          next.origin = { scroll: window.scrollY, source: next.design.number };
          history.replaceState({ ...history.state, memberDirectoryOrigin: next.origin }, "");
        }
      }
      setDialog(null);
      setView(next);
    };
    window.addEventListener("hashchange", navigate);
    return () => { window.removeEventListener("hashchange", navigate); history.scrollRestoration = saved; };
  }, []);

  useLayoutEffect(() => {
    const before = previous.current;
    if (view.kind === "members") {
      window.scrollTo(0, 0);
      focusControl(document.querySelector<HTMLElement>(".member-directory-heading h1"));
    } else if (view.kind === "organization" && before.kind === "members") {
      const saved = before.origin;
      if (saved?.source === view.design.number) {
        window.scrollTo(0, saved.scroll);
        focusControl(document.querySelector<HTMLElement>("#team-widget [data-directory-link]") ?? document.querySelector<HTMLElement>("#team-widget h2"));
      } else jumpToWidget();
    } else if (view.kind === "organization" && before.kind === "gallery") {
      window.scrollTo(0, 0);
      document.querySelector<HTMLElement>(".organization-hero h1")?.focus({ preventScroll: true });
    } else if (view.kind === "gallery" && view !== before) {
      const saved = before.kind === "gallery" ? null : galleryViews.current[view.links ? "links" : "standard"];
      const target = (saved && document.querySelector<HTMLElement>(`.team-gallery a[href="#${saved.source}"]`)) || document.querySelector<HTMLElement>(".team-gallery h1");
      window.scrollTo(0, saved?.scroll ?? 0);
      focusControl(target);
    }
    document.title = view.kind === "organization" ? `${view.design.number} · ${view.design.name} — Команда`
      : view.kind === "members" ? "Список участников · Северная лаборатория"
      : view.links ? "10 способов открыть список участников" : "20 вариантов виджета «Команда»";
    previous.current = view;
  }, [view]);

  useLayoutEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useLayoutEffect(() => {
    const element = toolbar.current!;
    const observer = new ResizeObserver(() => document.documentElement.style.setProperty("--team-toolbar-offset", `${element.offsetHeight + 14}px`));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  function jumpToWidget() {
    const widget = document.getElementById("team-widget");
    widget?.scrollIntoView({ block: "start" });
    widget?.querySelector("h2")?.focus({ preventScroll: true });
  }

  function updateWorkspace(change: (current: Workspace) => Workspace, message: string) {
    const trigger = opener.current;
    const scope = view.kind === "members" ? ".member-directory-page" : "#team-widget";
    const fallback = dialog?.kind === "member" ? `${scope} [data-member="${CSS.escape(dialog.id)}"]`
      : dialog?.kind === "invitation" ? "#team-widget .team-invite-action" : "#team-widget h2";
    setWorkspace(change);
    setDialog(null);
    setNotice(message);
    requestAnimationFrame(() => {
      if (document.activeElement !== document.body && document.activeElement !== trigger) return;
      focusControl(trigger?.isConnected && trigger.getClientRects().length ? trigger : document.querySelector<HTMLElement>(fallback));
    });
  }

  function resetWorkspace() {
    setWorkspace(initial);
    setNotice("Демоданные восстановлены.");
    requestAnimationFrame(() => {
      if (document.activeElement === document.body) focusControl(toolbar.current?.querySelector("select, .team-all-link") ?? null);
    });
  }

  function openDialog(next: DialogState) {
    if (next.kind === "members" && links && workspace.members.length > memberPreviewLimit && view.kind !== "gallery") {
      if (view.kind === "organization") window.location.hash = memberDirectoryHref(view.design.number);
      else setDialog(null);
      return;
    }
    setDialog(next);
  }

  return <div className="team-showcase" onFocus={(event) => {
    const control = event.target;
    if (links && control.matches(":is(a, button, summary, input, select):focus-visible") && control.closest(".team-preview-shell")) {
      requestAnimationFrame(() => {
        if (document.activeElement === control && control.matches(":focus-visible")) revealControl(control);
      });
    }
  }} onClickCapture={(event) => {
    if (!dialog && event.target instanceof Element) opener.current = event.target.closest("button");
  }}>
    <header className="team-preview-toolbar" ref={toolbar}>
      <Toolbar design={view.kind === "organization" ? view.design : null} gallery={view.kind === "gallery"} links={links} theme={theme}
        changeTheme={() => setTheme(theme === "light" ? "dark" : "light")} jumpToWidget={jumpToWidget} reset={workspace === initial ? undefined : resetWorkspace} />
    </header>
    <WorkspaceContext.Provider value={{ workspace, open: openDialog, section: "team", setSection: noAction }}>
      {view.kind === "gallery" && <TeamGallery pane={pane} select={setPane} sizeControl={sizeControl} />}
      {view.kind === "organization" && <OrganizationPage design={view.design} pane={pane} select={setPane} sizeControl={sizeControl}
        memberSearch={links ? { value: widgetSearch, setValue: setWidgetSearch } : undefined} />}
      {view.kind === "members" && <PreviewShell><MemberDirectory key={`${view.design.number}:${view.search}`} number={view.design.number} initialSearch={view.search} /></PreviewShell>}
      {notice && <p className="preview-notice" role="status"><Icon name="check" size={18} />{notice}</p>}
      {dialog && <WorkspaceDialog state={dialog} theme={theme} opener={opener.current} close={() => setDialog(null)} update={updateWorkspace} />}
    </WorkspaceContext.Provider>
  </div>;
}

createRoot(document.getElementById("root")!).render(<I18nProvider><TeamPreview /></I18nProvider>);
