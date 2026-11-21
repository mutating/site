import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type MouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, request, withDeadline } from "../lib/api";
import { GiB, number, stepPath } from "../lib/chart";
import { computerAvailability } from "../lib/computerAvailability";
import { usePoolRead, useReloadOnComputerPoolChange, type ConnectedPoolOrigin } from "../lib/compute";
import { useI18n } from "../lib/i18n";
import { runtimeLabel } from "../lib/runtime";
import { useSession } from "../lib/session";
import type { CapacityHistory, Computer, OrganizationMembershipCheck, Pool, PoolHistory, Summary } from "../lib/types";
import { useData, useRefreshWhileVisible } from "../lib/useData";
import { CapacityChart } from "./CapacityChart";
import { Icon, Loading, RetryErrorBox, useRestoreFocusOnRemoval } from "./UI";

export type OrganizationResourceAccess = "available" | "checking" | "denied" | "unverified";
type ResourcePool = Pick<Pool, "kind" | "id">;
type PoolSnapshot = { inventory: Summary; available: Summary; computers: Computer[]; check_revision?: number };
type WidgetRead<T> = Pick<ReturnType<typeof useData<T>>, "data" | "error" | "loading" | "reload">;
type ResourceAccessCheck = (error: ApiError) => boolean;

export function usePoolPointerLayout(holdContent?: (control: HTMLElement) => (() => void) | undefined) {
  const gesture = useRef<AbortController | null>(null);
  useEffect(() => () => gesture.current?.abort(), []);

  return (event: ReactPointerEvent<HTMLElement>) => {
    if (!event.isTrusted || !event.isPrimary || event.button !== 0 || !(event.target instanceof Element)) return;
    const control = event.target.closest<HTMLElement>('a[href], button, input, select, textarea, summary, [role="slider"], .tmc-select');
    if (!control) return;
    const openSelect = event.target.closest(".tmc-select__panel");
    gesture.current?.abort();
    const detail = event.currentTarget.closest(".organization-detail");
    const releaseContent = holdContent?.(control);
    if (!detail && !releaseContent) return;
    const sections = detail
      ? detail.querySelectorAll<HTMLElement>(".organization-hero, .organization-overview, .organization-computers, .organization-people, .organization-member-results, .organization-history-grid")
      : event.currentTarget.querySelectorAll<HTMLElement>(".connected-computer-row");
    const layout = Array.from(sections, (section) => ({
      section,
      size: section.getBoundingClientRect().height,
      blockSize: section.style.blockSize,
      overflow: section.style.overflow,
    }));
    const pointerId = event.pointerId;
    const controller = new AbortController();
    gesture.current = controller;
    const options = { capture: true, signal: controller.signal };
    const pressedHref = control.getAttribute("href");
    let released = false;
    let expiry: number | undefined;
    const abort = () => controller.abort();

    // A response can move the control before the browser dispatches its click.
    for (const { section, size } of layout) {
      section.style.blockSize = `${size}px`;
      if (!openSelect || !section.contains(openSelect)) section.style.overflow = "clip";
    }
    window.addEventListener("pointerup", (up) => {
      if (up.pointerId !== pointerId) return;
      released = true;
      // Touch browsers dispatch click after pointerup. A canceled gesture may send no click.
      expiry = window.setTimeout(abort, up.pointerType === "touch" ? 400 : 100);
    }, options);
    window.addEventListener("click", (click) => {
      if (!released || click.detail === 0) return;
      if (click.target instanceof Node && (!control.contains(click.target) || control.getAttribute("href") !== pressedHref)) {
        click.preventDefault();
        click.stopPropagation();
      }
      window.clearTimeout(expiry);
      expiry = window.setTimeout(abort);
    }, options);
    window.addEventListener("pointercancel", (cancel) => {
      if (cancel.pointerId === pointerId) abort();
    }, options);
    window.addEventListener("pointerdown", abort, options);
    window.addEventListener("blur", abort, { signal: controller.signal });
    controller.signal.addEventListener("abort", () => {
      window.clearTimeout(expiry);
      for (const { section, blockSize, overflow } of layout) {
        section.style.blockSize = blockSize;
        section.style.overflow = overflow;
      }
      releaseContent?.();
    }, { once: true });
  };
}

function useResourceData<T>(
  pool: ResourcePool,
  read: (signal: AbortSignal) => Promise<T>,
  key: string,
  { enabled = true, refreshMs = 60_000, revision, retainError = pool.kind === "users", onAccessError }: {
    enabled?: boolean; refreshMs?: number; revision?: number; retainError?: boolean; onAccessError?: ResourceAccessCheck;
  } = {},
) {
  const userId = useSession().user!.id;
  const state = usePoolRead(read, key, true, { revision, retainError, onAccessError });
  const { reload } = state;
  useReloadOnComputerPoolChange(userId, () => { if (enabled) reload(); }, pool.kind === "companies" ? pool.id : null);
  useRefreshWhileVisible(reload, enabled, refreshMs);
  return state;
}

export function usePoolOverview(pool: ResourcePool, { enabled = true, revision, onAccessError, onRead }: {
  enabled?: boolean;
  revision?: number;
  onAccessError?: ResourceAccessCheck;
  onRead?: (membership: OrganizationMembershipCheck) => void;
} = {}) {
  const path = `${pool.kind}/${pool.id}/resources/pool-overview`;
  const recordSnapshot = (snapshot: PoolSnapshot, membershipId?: string) => {
    const revision = snapshot.check_revision;
    if (membershipId && revision !== undefined) onRead?.({ id: membershipId, revision });
  };
  const state = useResourceData(pool, async (signal) => {
    if (!enabled) return null;
    const read = (readSignal: AbortSignal) => request<PoolSnapshot>(path, { signal: readSignal });
    const snapshot = await (pool.kind === "users" ? read(signal) : withDeadline(signal, read));
    if (!signal.aborted) recordSnapshot(snapshot.data, snapshot.organizationMembershipId);
    return snapshot.data;
  }, `${path}/${enabled}`, { enabled, revision, retainError: true, onAccessError });
  const viewer = useSession().user!.id;
  const companyId = pool.kind === "companies" ? pool.id : null;
  const [departures, setDepartures] = useState<ReadonlyMap<string, number>>(() => new Map());
  const [arrivalRevision, setArrivalRevision] = useState(-1);
  useReloadOnComputerPoolChange(viewer, ({ computerId, assignment }) => {
    if (!enabled || !assignment) return;
    const snapshotRevision = state.data?.check_revision ?? -1;
    const inPool = assignment.companyId === companyId && assignment.canRead && assignment.assigned !== false;
    if (inPool && assignment.assigned && assignment.revision > snapshotRevision) {
      setArrivalRevision((previous) => Math.max(previous, assignment.revision));
    }
    setDepartures((previous) => {
      if (snapshotRevision >= assignment.revision || inPool && !previous.has(computerId) ||
          !inPool && !!companyId && assignment.companyId === companyId) return previous;
      const next = new Map(previous);
      if (inPool) next.delete(computerId);
      else next.set(computerId, assignment.revision);
      return next;
    });
  }, companyId);
  return { ...state, departures, arrivalRevision, recordSnapshot };
}

export function PoolOverview({ state, computerCount, memberCount, personal = false, access = "available" }: {
  state: WidgetRead<PoolSnapshot>;
  computerCount?: number;
  memberCount?: number;
  personal?: boolean;
  access?: OrganizationResourceAccess;
}) {
  const { t, locale } = useI18n();
  const resourcesVisible = access === "available";
  const data = resourcesVisible && !state.error ? state.data : null;
  const cpu = data?.inventory.resources.find((resource) => resource.code === "cpu");
  const memory = data?.inventory.resources.find((resource) => resource.code === "memory");
  const freeCpu = data?.available.resources.find((resource) => resource.code === "cpu")?.free ?? 0;
  const freeMemory = data?.available.resources.find((resource) => resource.code === "memory")?.free ?? 0;
  const dash = "—";
  const formatCount = (count?: number) => count === undefined ? dash : number(count, locale);
  const metrics: { icon: string; label: string; value: string; detail?: string; testId?: string }[] = [
    { icon: "computer", label: t("computers"), value: formatCount(data?.inventory.computer_count ?? computerCount), detail: data && !personal ? `${number(data.available.computer_count, locale)} ${t("organizationAvailableCount")}` : undefined },
    { icon: "cpu", label: t("organizationCpuFree"), value: data ? number(freeCpu / 1000, locale) : dash, detail: data ? `${t("of")} ${number((cpu?.total ?? 0) / 1000, locale)} ${t("cores")}` : undefined },
    { icon: "memory", label: t("organizationMemoryFree"), value: data ? number(freeMemory / GiB, locale) : dash, detail: data ? `${t("of")} ${number((memory?.total ?? 0) / GiB, locale)} GiB` : undefined },
    personal
      ? { icon: "compute", label: t("availableComputers"), value: formatCount(data?.available.computer_count), testId: "computer-count" }
      : { icon: "users", label: t("members"), value: formatCount(memberCount) },
  ];
  return <section className="organization-widget organization-overview" aria-label={t(personal ? "personalPoolOverview" : "organizationOverview")}
    aria-busy={access === "checking" || resourcesVisible && state.loading}>
    <dl className="organization-overview-metrics">
      {metrics.map(({ icon, label, value, detail, testId }) => <div key={icon}>
        <dt><Icon name={icon} size={17} />{label}</dt>
        <dd data-testid={testId}>{value}</dd>
        {detail && <dd className="organization-metric-detail">{detail}</dd>}
      </div>)}
    </dl>
  </section>;
}

export function PoolComputers({ state, computerCount, query, personal = false, access = "available", onAccessError }: {
  state: WidgetRead<PoolSnapshot> & { departures: ReadonlyMap<string, number>; arrivalRevision: number };
  computerCount?: number;
  query: string;
  personal?: boolean;
  access?: OrganizationResourceAccess;
  onAccessError?: (error: ApiError) => void;
}) {
  const { t, locale } = useI18n();
  const resourcesVisible = access === "available";
  const navigate = useNavigate();
  const connectedPoolOrigin: ConnectedPoolOrigin | undefined = useLocation().state?.connectedPoolOrigin;
  const navigationState = connectedPoolOrigin ? { connectedPoolOrigin } : undefined;
  const lookup = useRef<{ controller: AbortController; computerId: string } | null>(null);
  const [lookupFailure, setLookupFailure] = useState<{ computerId: string; target: string; error: unknown } | null>(null);
  const [held, setHeld] = useState<{ computers?: Computer[]; error: unknown; loading: boolean; revision?: number } | null>(null);
  const denied = state.error instanceof ApiError && state.error.status >= 400 && state.error.status < 500;
  const visible = resourcesVisible && !denied ? held : null;
  const visibleError = visible ? visible.error : state.error;
  const count = (resourcesVisible && !visibleError ? state.data?.inventory.computer_count : null) ?? computerCount;
  const revision = state.data?.check_revision;
  const departed = useCallback((computerId: string, snapshotRevision?: number) =>
    (state.departures.get(computerId) ?? -1) > (snapshotRevision ?? -1), [state.departures]);
  const currentComputers = state.data?.computers.filter((computer) => !departed(computer.id, revision));
  const computers = visible ? visible.computers : currentComputers;
  const awaitingArrival = state.arrivalRevision > (revision ?? -1);
  const loading = state.loading && (!state.data || currentComputers?.length === 0 &&
    (state.data.computers.length > 0 || awaitingArrival));
  const holdLayout = usePoolPointerLayout(() => {
    setHeld({ computers: currentComputers, error: state.error, loading, revision });
    return () => setHeld(null);
  });
  useLayoutEffect(() => {
    if (!resourcesVisible || denied) setHeld(null);
  }, [resourcesVisible, denied]);
  useLayoutEffect(() => {
    setLookupFailure(null);
    return () => lookup.current?.controller.abort();
  }, [query, resourcesVisible]);
  useLayoutEffect(() => {
    const pending = lookup.current;
    if (pending && departed(pending.computerId, revision)) pending.controller.abort();
    if (lookupFailure && departed(lookupFailure.computerId, revision)) setLookupFailure(null);
  }, [departed, revision, lookupFailure]);
  const openUnlisted = (computerId: string, target: string) => {
    lookup.current?.controller.abort();
    const controller = new AbortController();
    lookup.current = { controller, computerId };
    const cancel = () => controller.abort();
    for (const type of ["pointerdown", "keydown"]) {
      window.addEventListener(type, cancel, { capture: true, signal: controller.signal });
    }
    const scoped = new URLSearchParams(query);
    scoped.set("computer_id", computerId);
    scoped.set("limit", "1");
    void withDeadline(controller.signal, (signal) => api<Computer[]>(`computers?${scoped}`, { signal }))
      .then((matched) => {
        if (controller.signal.aborted) return;
        setLookupFailure(null);
        if (matched.some((computer) => computer.id === computerId)) navigate(target, { state: navigationState });
      })
      .catch((error) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && (error.status === 403 || error.status === 404)) {
          setLookupFailure(null);
          onAccessError?.(error);
        } else {
          setLookupFailure({ computerId, target, error });
        }
      })
      .finally(cancel);
  };
  const inventoryLink = useRef<HTMLAnchorElement>(null);
  const restoreFocus = useCallback(() => {
    if (document.activeElement === document.body) inventoryLink.current?.focus();
  }, []);
  return <section className="organization-widget organization-computers" data-empty={count === 0 || undefined}
    aria-label={t("computers")} aria-busy={access === "checking" || resourcesVisible && state.loading}
    onPointerDownCapture={holdLayout}>
    <header className="organization-widget-header">
      <h2>{t("computers")} {count !== undefined && <span className="organization-widget-count" aria-hidden="true">{number(count, locale)}</span>}</h2>
      {resourcesVisible && <Link ref={inventoryLink} className="text-link" to={`/compute/computers?${query}`}
        state={navigationState}>{t("viewComputers")}</Link>}
    </header>
    {access === "checking" && <Loading />}
    {access === "denied" && <p className="muted">{t("resourcesReadDenied")}</p>}
    {resourcesVisible && (visibleError ? <RetryErrorBox error={visibleError} retry={state.reload} restoreFocus={restoreFocus} /> :
      (visible ? visible.loading : loading) ? <Loading /> : !computers?.length ? <p className="muted">{t(personal ? "noPersonalComputers" : "noOrganizationComputers")}</p> :
      <ul className="organization-computer-cards">
        {computers.map((computer) => {
          const missing = visible !== null && !currentComputers?.some((current) => current.id === computer.id);
          const completeInventory = count !== undefined && count <= (currentComputers?.length ?? 0);
          const withdrawn = !!visible && departed(computer.id, visible.revision);
          return <PoolComputerCard key={`${computer.id}/${withdrawn}`} computer={computer} query={query} navigationState={navigationState}
            withdrawn={withdrawn}
            unavailable={missing && completeInventory} verify={missing && !completeInventory ? openUnlisted : undefined}
            restoreFocus={restoreFocus} />;
        })}
      </ul>)}
    {resourcesVisible && lookupFailure && <RetryErrorBox error={lookupFailure.error} restoreFocus={restoreFocus} retryLabel="retryOpenComputer"
      retry={() => openUnlisted(lookupFailure.computerId, lookupFailure.target)} />}
  </section>;
}

function PoolComputerCard({ computer, query, navigationState, unavailable, withdrawn, verify, restoreFocus }: {
  computer: Computer;
  query: string;
  navigationState?: { connectedPoolOrigin: ConnectedPoolOrigin };
  unavailable: boolean;
  withdrawn: boolean;
  verify?: (id: string, target: string) => void;
  restoreFocus: () => void;
}) {
  const { t, locale } = useI18n();
  const { status, available } = computerAvailability(computer);
  const link = useRef<HTMLAnchorElement>(null);
  useRestoreFocusOnRemoval(link, restoreFocus);
  const target = `/compute/computers/${computer.id}?${query}`;
  const check = (event: MouseEvent<HTMLAnchorElement>) => {
    if (unavailable) {
      event.preventDefault();
    } else if (verify && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      verify(computer.id, target);
    }
  };
  const contents = <>
      <div className="organization-computer-top">
        <span className="organization-computer-icon"><Icon name="computer" size={20} /></span>
        <span className={`organization-computer-status ${status}`}><i aria-hidden="true" />{t(status)}</span>
      </div>
      <div className="organization-computer-identity">
        <strong>{computer.name || computer.id.slice(0, 8)}</strong>
        <small>{computer.os} · {computer.architecture} · {runtimeLabel(computer.runtime)}</small>
      </div>
      <div className="organization-computer-capacity">
        <span>CPU · {t("free")}</span>
        <strong>{available ? number(computer.free_cpu / 1000, locale) : "—"} / {number(computer.cpu_millis / 1000, locale)}</strong>
      </div>
  </>;
  return <li aria-hidden={withdrawn || undefined}>
    {withdrawn ? <div className="organization-computer-card" style={{ visibility: "hidden" }}>{contents}</div> :
      <Link ref={link} className="organization-computer-card" to={target} state={navigationState}
        aria-disabled={unavailable || undefined} tabIndex={unavailable ? -1 : undefined} onClick={check}>{contents}</Link>}
  </li>;
}

export function PersonalPoolResources({ userId, query, revision }: { userId: string; query: string; revision: number }) {
  const pool = { kind: "users", id: userId } as const;
  const state = usePoolOverview(pool, { revision });
  return <>
    <PoolOverview state={state} personal />
    <PoolComputers state={state} query={query} personal />
    <PersonalPoolHistory pool={pool} revision={revision} overview={state} />
  </>;
}

const fields = {
  cpu: { total: "cpu_total_millis", free: "cpu_free_millis", divisor: 1000 },
  memory: { total: "memory_total_bytes", free: "memory_free_bytes", divisor: GiB },
} as const;

function ResourceChart({ history, kind, title }: { history: PoolHistory; kind: keyof typeof fields; title: string }) {
  const id = useId();
  const { t, locale } = useI18n();
  const field = fields[kind];
  const [showData, setShowData] = useState(false);
  const latest = history.points.at(-1);
  if (!latest) return <p className="muted">{t("noHistory")}</p>;
  const start = Date.parse(history.from_time);
  const end = Date.parse(history.to_time);
  const peak = history.points.reduce((maximum, point) => Math.max(maximum, point[field.total], point[field.free]), field.divisor);
  const order = 10 ** Math.floor(Math.log10(peak / field.divisor));
  const max = Math.ceil(peak / (order * field.divisor)) * order * field.divisor;
  const value = (amount: number) => number(amount / field.divisor, locale);
  const compact = new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 });
  const ticks = [1, .5, 0].map(fraction => ({ fraction, label: max * fraction / field.divisor >= 10_000
    ? compact.format(max * fraction / field.divisor) : value(max * fraction) }));
  const left = Math.max(39, ...ticks.map(({ label }) => label.length * 7.5 + 11));
  const plot = { width: 407 - left, height: 98, x: left, y: 11 };
  const seriesOrder = history.sampled ? ["total", "free"] as const : ["free", "total"] as const;
  function path(series: "total" | "free") {
    const key = field[series];
    if (!history.sampled) return stepPath(history.points, key, start, end, max, plot.width, plot.height);
    // Sampled readings have known times and values, but omitted transitions.
    return history.points.map((point) => {
      const x = (Date.parse(point.at) - start) / (end - start) * plot.width;
      const y = (1 - point[key] / max) * plot.height;
      return `M${x.toFixed(2)},${y.toFixed(2)}h.01`;
    }).join("");
  }
  const date = new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" });
  const timestamp = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "medium" });
  const unit = kind === "memory" ? "GiB" : t("cores");
  return <figure className={`organization-resource-chart ${kind}${history.sampled ? " is-sampled" : ""}`}>
    <figcaption id={`${id}-summary`}>
      <span><i className="total" />{t("total")} <strong>{value(latest[field.total])} {unit}</strong></span>
      <span><i className="free" />{t("free")} <strong>{value(latest[field.free])} {unit}</strong></span>
      {history.sampled && <span className="organization-chart-sample">{t("organizationHistorySampled")}</span>}
    </figcaption>
    <svg viewBox="0 0 419 137" role="img" aria-labelledby={`${id}-title`} aria-describedby={`${id}-summary`}>
      <title id={`${id}-title`}>{title} · {t("sevenDays")}</title>
      {ticks.map(({ fraction, label }) => {
        const y = plot.y + plot.height * (1 - fraction);
        return <g key={fraction}>
          <line className="chart-grid" x1={plot.x} x2={plot.x + plot.width} y1={y} y2={y} />
          <text className="tick" x={plot.x - 6} y={y + 3} textAnchor="end">{label}</text>
        </g>;
      })}
      <g transform={`translate(${plot.x} ${plot.y})`}>
        {seriesOrder.map(series => <path key={series} className={series} d={path(series)} />)}
      </g>
      <text className="tick" x={plot.x} y="130">{date.format(start)}</text>
      <text className="tick" x={plot.x + plot.width} y="130" textAnchor="end">{date.format(end)}</text>
    </svg>
    <details className="chart-data" onToggle={(event) => setShowData(event.currentTarget.open)}>
      <summary>{t("chartTable")}</summary>
      {showData && <div className="table-scroll" tabIndex={0}>
        <table>
          <thead><tr><th>{t("time")}</th><th>{t("total")} · {unit}</th><th>{t("free")} · {unit}</th></tr></thead>
          <tbody>{history.points.map((point) => <tr key={point.at}>
            <td><time dateTime={point.at}>{timestamp.format(Date.parse(point.at))}</time></td>
            <td>{value(point[field.total])}</td><td>{value(point[field.free])}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    </details>
  </figure>;
}

function usePoolSnapshotCorrection<T extends CapacityHistory | PoolHistory>(
  pool: ResourcePool,
  overview: ReturnType<typeof usePoolOverview>,
  state: ReturnType<typeof useData<T>>,
  differs: (snapshot: PoolSnapshot, history: T) => boolean,
  onAccessError?: ResourceAccessCheck,
) {
  const { data: history, error, loading, update: updateHistory } = state;
  const { data: snapshot, error: overviewError, loading: overviewLoading, update: updateOverview } = overview;
  const mismatch = snapshot && history && !loading && !error && !overviewLoading && !overviewError &&
    snapshot.inventory.as_of !== history.to_time && differs(snapshot, history);
  const path = `${pool.kind}/${pool.id}/resources/pool-snapshot`;
  const correctionKey = mismatch ? `${path}/${snapshot.inventory.as_of}/${history.to_time}` : path;
  const correction = usePoolRead(async (signal) => {
    if (!mismatch) return;
    const current = await withDeadline(signal, (readSignal) => request<{ overview: PoolSnapshot; history: T }>(
      path, { signal: readSignal },
    ));
    if (!signal.aborted) {
      overview.recordSnapshot(current.data.overview, current.organizationMembershipId);
      updateOverview(current.data.overview);
      updateHistory(current.data.history);
    }
  }, correctionKey, false, { retainError: true, onAccessError });
  const correctingFailure = !!mismatch && !!correction.error;
  useRefreshWhileVisible(correction.reload, correctingFailure && !correction.loading, 5_000);
  return correctingFailure ? { ...state, error: correction.error, reload: correction.reload } : state;
}

function useHoldChartError(error: unknown) {
  const [held, setHeld] = useState<{ error: unknown } | null>(null);
  const holdLayout = usePoolPointerLayout(() => {
    setHeld({ error });
    return () => setHeld(null);
  });
  const denied = error instanceof ApiError && error.status >= 400 && error.status < 500;
  return { error: denied || held === null ? error : held.error, holdLayout };
}

export function OrganizationResourceCharts({ companyId, overview, onRead, onAccessError }: {
  companyId: string;
  overview: ReturnType<typeof usePoolOverview>;
  onRead: (membership: OrganizationMembershipCheck) => void;
  onAccessError: ResourceAccessCheck;
}) {
  const pool = { kind: "companies", id: companyId } as const;
  const state = useResourceData(pool, async (signal) => {
    const { data, organizationMembershipId, poolCheckRevision } = await withDeadline(signal, (querySignal) =>
      request<PoolHistory>(`companies/${companyId}/resources/pool-history`, { signal: querySignal }),
    );
    if (!signal.aborted && organizationMembershipId && poolCheckRevision !== undefined) {
      onRead({ id: organizationMembershipId, revision: poolCheckRevision });
    }
    return data;
  }, companyId, { refreshMs: 300_000, retainError: true, onAccessError });
  const chartState = usePoolSnapshotCorrection(pool, overview, state, (snapshot, history) => {
    const latest = history.points.at(-1);
    return (["cpu", "memory"] as const).some((kind) => {
      const total = snapshot.inventory.resources.find((resource) => resource.code === kind)?.total ?? 0;
      const free = snapshot.available.resources.find((resource) => resource.code === kind)?.free ?? 0;
      return total !== (latest?.[fields[kind].total] ?? 0) || free !== (latest?.[fields[kind].free] ?? 0);
    });
  }, onAccessError);
  const { error, holdLayout } = useHoldChartError(chartState.error);
  const visibleState = { ...chartState, error };
  return <div className="organization-history-grid" onPointerDownCapture={holdLayout}>
    <OrganizationHistoryWidget kind="cpu" state={visibleState} />
    <OrganizationHistoryWidget kind="memory" state={visibleState} />
  </div>;
}

function OrganizationHistoryWidget({ kind, state }: { kind: keyof typeof fields; state: WidgetRead<PoolHistory> }) {
  const { t } = useI18n();
  const title = t(kind === "cpu" ? "organizationCpuHistory" : "organizationMemoryHistory");
  const widget = useRef<HTMLElement>(null);
  const restoreFocus = useCallback(() => {
    if (document.activeElement === document.body) widget.current?.focus();
  }, []);
  const error = state.error;
  const history = error instanceof ApiError && error.status >= 400 && error.status < 500 ? null : state.data;
  return <section ref={widget} className="organization-widget organization-history" aria-label={title} aria-busy={state.loading} tabIndex={-1}>
    <header className="organization-widget-header"><h2><Icon name={kind} size={17} />{title}</h2><span className="eyebrow">{t("sevenDays")}</span></header>
    {!!error && <RetryErrorBox error={error} retry={state.reload} restoreFocus={restoreFocus} />}
    {history ? <ResourceChart history={history} kind={kind} title={title} /> : state.loading && !error && <Loading />}
  </section>;
}

function PersonalPoolHistory({ pool, revision, overview }: {
  pool: ResourcePool;
  revision: number;
  overview: ReturnType<typeof usePoolOverview>;
}) {
  const { t } = useI18n();
  const widget = useRef<HTMLDivElement>(null);
  const restoreFocus = useCallback(() => {
    if (document.activeElement === document.body) widget.current?.focus();
  }, []);
  const historyState = useResourceData(pool, (signal) =>
    api<CapacityHistory>(`users/${pool.id}/resources/availability-history`, { signal }), pool.id, { revision });
  const state = usePoolSnapshotCorrection(pool, overview, historyState, (snapshot, history) => {
    if (history.to_time !== history.as_of) return false;
    const latest = history.points.at(-1);
    const cpu = snapshot.available.resources.find((resource) => resource.code === "cpu");
    const memory = snapshot.available.resources.find((resource) => resource.code === "memory");
    return (cpu?.total ?? 0) !== (latest?.cpu_millis ?? 0) ||
      (cpu?.occupied ?? 0) !== (latest?.cpu_occupied_millis ?? 0) ||
      (memory?.total ?? 0) !== (latest?.memory_bytes ?? 0) ||
      (memory?.occupied ?? 0) !== (latest?.memory_occupied_bytes ?? 0);
  });
  const { error, holdLayout } = useHoldChartError(state.error);
  return <div ref={widget} className="personal-pool-history" role="group" aria-label={t("history")}
    aria-busy={state.loading} tabIndex={-1} onPointerDownCapture={holdLayout}>
    {!!error && <RetryErrorBox error={error} retry={state.reload} restoreFocus={restoreFocus} />}
    {state.data ? <CapacityChart history={state.data} /> : state.loading && !error && <Loading />}
  </div>;
}
