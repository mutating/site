import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { api, request, withDeadline } from "../lib/api";
import { GiB, number, stepPath } from "../lib/chart";
import { computerAvailability } from "../lib/computerAvailability";
import { useI18n } from "../lib/i18n";
import { runtimeLabel } from "../lib/runtime";
import type { Company, Computer, PoolHistory, Summary } from "../lib/types";
import { useData } from "../lib/useData";
import { ErrorBox, Icon, Loading } from "./UI";

export type OrganizationResourceAccess = "available" | "checking" | "denied" | "unverified";

function useResourceData<T>(read: (signal: AbortSignal) => Promise<T>, key: string, enabled = true, refreshMs = 60_000) {
  const state = useData(read, key, true);
  const { reload } = state;
  useEffect(() => {
    if (!enabled) return;
    const refresh = () => { if (document.visibilityState === "visible") reload(); };
    const timer = window.setInterval(refresh, refreshMs);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [enabled, refreshMs, reload]);
  return state;
}

export function OrganizationOverview({ company, memberCount, access }: {
  company: Company;
  memberCount: number;
  access: OrganizationResourceAccess;
}) {
  const { t, locale } = useI18n();
  const resourcesVisible = access === "available";
  const state = useResourceData((signal) => resourcesVisible ? withDeadline(signal, (querySignal) =>
    api<{ inventory: Summary; available: Summary }>(`companies/${company.id}/resources/pool-summary`, { signal: querySignal }),
  ) : Promise.resolve(null), `${company.id}/${resourcesVisible}`, resourcesVisible);
  const data = resourcesVisible && !state.error ? state.data : null;
  const cpu = data?.inventory.resources.find((resource) => resource.code === "cpu");
  const memory = data?.inventory.resources.find((resource) => resource.code === "memory");
  const freeCpu = data?.available.resources.find((resource) => resource.code === "cpu")?.free ?? 0;
  const freeMemory = data?.available.resources.find((resource) => resource.code === "memory")?.free ?? 0;
  const dash = "—";
  const metrics = [
    { icon: "computer", label: t("computers"), value: number(data?.inventory.computer_count ?? company.computer_count, locale), detail: data ? `${number(data.available.computer_count, locale)} ${t("organizationAvailableCount")}` : undefined },
    { icon: "cpu", label: t("organizationCpuFree"), value: data ? number(freeCpu / 1000, locale) : dash, detail: data ? `${t("of")} ${number((cpu?.total ?? 0) / 1000, locale)} ${t("cores")}` : undefined },
    { icon: "memory", label: t("organizationMemoryFree"), value: data ? number(freeMemory / GiB, locale) : dash, detail: data ? `${t("of")} ${number((memory?.total ?? 0) / GiB, locale)} GiB` : undefined },
    { icon: "users", label: t("members"), value: number(memberCount, locale) },
  ];
  return <section className="organization-widget organization-overview" aria-label={t("organizationOverview")}
    aria-busy={access === "checking" || resourcesVisible && state.loading}>
    <dl className="organization-overview-metrics">
      {metrics.map(({ icon, label, value, detail }) => <div key={icon}>
        <dt><Icon name={icon} size={17} />{label}</dt>
        <dd>{value}</dd>
        {detail && <dd className="organization-metric-detail">{detail}</dd>}
      </div>)}
    </dl>
    {resourcesVisible && <ErrorBox error={state.error} retry={state.reload} />}
  </section>;
}

export function OrganizationComputers({ company, query, access }: {
  company: Company;
  query: string;
  access: OrganizationResourceAccess;
}) {
  const { t, locale } = useI18n();
  const resourcesVisible = access === "available";
  const state = useResourceData((signal) => resourcesVisible ? withDeadline(signal, (readSignal) =>
    request<Computer[]>(`computers?company_id=${company.id}&only_available=false&limit=3`, { signal: readSignal }),
  ) : Promise.resolve(null), `${company.id}/${resourcesVisible}`, resourcesVisible);
  const count = (resourcesVisible ? state.data?.total : null) ?? company.computer_count;
  return <section className="organization-widget organization-computers" data-empty={count === 0 || undefined}
    aria-label={t("computers")} aria-busy={access === "checking" || resourcesVisible && state.loading}>
    <header className="organization-widget-header">
      <h2>{t("computers")} <span className="organization-widget-count" aria-hidden="true">{number(count, locale)}</span></h2>
      {resourcesVisible && <Link className="text-link" to={`/compute/computers?${query}`}>{t("viewComputers")}</Link>}
    </header>
    {access === "checking" && <Loading />}
    {access === "denied" && <p className="muted">{t("resourcesReadDenied")}</p>}
    {resourcesVisible && (state.loading && !state.data ? <Loading /> : state.error ?
      <ErrorBox error={state.error} retry={state.reload} /> : !state.data?.data.length ? <p className="muted">{t("noOrganizationComputers")}</p> :
      <ul className="organization-computer-cards">
        {state.data.data.map((computer) => {
          const { status, available } = computerAvailability(computer);
          return <li key={computer.id}>
            <Link className="organization-computer-card" to={`/compute/computers/${computer.id}?${query}`}>
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
            </Link>
          </li>;
        })}
      </ul>)}
  </section>;
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

export function OrganizationResourceCharts({ companyId }: { companyId: string }) {
  const { t } = useI18n();
  const state = useResourceData((signal) => withDeadline(signal, (querySignal) => api<PoolHistory>(
    `companies/${companyId}/resources/pool-history`, { signal: querySignal },
  )), companyId, true, 300_000);
  return <div className="organization-history-grid">
    {(["cpu", "memory"] as const).map((kind) => {
      const title = t(kind === "cpu" ? "organizationCpuHistory" : "organizationMemoryHistory");
      return <section className="organization-widget organization-history" key={kind} aria-label={title} aria-busy={state.loading}>
        <header className="organization-widget-header"><h2><Icon name={kind} size={17} />{title}</h2><span className="eyebrow">{t("sevenDays")}</span></header>
        {state.loading && !state.data ? <Loading /> : state.error ? <ErrorBox error={state.error} retry={state.reload} /> : state.data && <ResourceChart history={state.data} kind={kind} title={title} />}
      </section>;
    })}
  </div>;
}
