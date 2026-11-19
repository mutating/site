import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useData } from "../lib/useData";
import { useI18n } from "../lib/i18n";
import { GiB, day, number } from "../lib/chart";
import type { Pool, Summary, CapacityHistory, Company } from "../lib/types";
import { CapacityChart } from "./CapacityChart";
import { ErrorBox, Icon, Loading } from "./UI";
export function RestrictedPoolCard({ company }: { company: Company }) {
  const { t } = useI18n();
  return (
    <section className="pool-card" aria-label={company.name}>
      <header className="pool-card-header">
        <div>
          <p className="eyebrow">{t("organizationType")}</p>
          <h2>{company.name}</h2>
        </div>
        <Link className="text-link" to={`/organizations/${company.id}`}>
          {t("members")} ↗
        </Link>
      </header>
      <p className="muted">{t("resourcesReadDenied")}</p>
    </section>
  );
}
export function Metrics({
  summary,
  compact = false,
}: {
  summary: Summary;
  compact?: boolean;
}) {
  const { t, locale } = useI18n();
  const cpu = summary.resources.find((r) => r.code === "cpu");
  const memory = summary.resources.find((r) => r.code === "memory");
  const amount = (value: number) => {
    const formatted = number(value, locale);
    if (!compact) return formatted;
    if (value > 0 && value < 0.01) {
      return new Intl.NumberFormat(locale, {
        maximumSignificantDigits: 3,
        notation: value < 0.0001 ? "scientific" : "standard",
      }).format(value);
    }
    if (value >= 10_000 && formatted.length > 8) {
      const short = new Intl.NumberFormat(locale, {
        notation: "compact",
        maximumSignificantDigits: 3,
      }).format(value);
      return <abbr title={formatted}>{short}</abbr>;
    }
    return formatted;
  };
  return (
    <div className="metrics">
      <div className="metric">
        <span className="metric-label">
          <Icon name="cpu" size={17} />
          CPU
        </span>
        <div className="metric-value">
          <span className="metric-free">
            {amount((cpu?.free || 0) / 1000)}
          </span>
          <span className="metric-total">
            / {amount((cpu?.total || 0) / 1000)}
          </span>
        </div>
        <small>
          {t("cores")} · {t("free")} / {t("total")}
        </small>
        <div className="meter">
          <span
            style={{
              width: `${cpu?.total ? Math.min(100, (cpu.free / cpu.total) * 100) : 0}%`,
            }}
          />
        </div>
      </div>
      <div className="metric">
        <span className="metric-label">
          <Icon name="memory" size={17} />
          {t("memory")}
        </span>
        <div className="metric-value">
          <span className="metric-free">
            {amount((memory?.free || 0) / GiB)}
          </span>
          <span className="metric-total">
            / {amount((memory?.total || 0) / GiB)}
          </span>
        </div>
        <small>
          GiB · {t("free")} / {t("total")}
        </small>
        <div className="meter memory-meter">
          <span
            style={{
              width: `${memory?.total ? Math.min(100, (memory.free / memory.total) * 100) : 0}%`,
            }}
          />
        </div>
      </div>
      <div className="metric">
        <span className="metric-label">
          <Icon name="computer" size={17} />
          {t("connected")}
        </span>
        <div className="metric-value" data-testid="computer-count">
          {amount(summary.computer_count)}
        </div>
        <small>
          <span
            className={`signal-dot ${summary.computer_count ? "" : "inactive"}`}
          />
          {t("onlineOnly")}
        </small>
      </div>
    </div>
  );
}
export function PoolCard({
  pool,
  chart = false,
  revision = 0,
}: {
  pool: Pool;
  chart?: boolean;
  revision?: number;
}) {
  const { t } = useI18n();
  const state = useData(async (signal) => {
    const now = new Date();
    const from = new Date(now.getTime() - 7 * day);
    const prefix = `${pool.kind}/${pool.id}/resources`;
    const [summary, history] = await Promise.all([
      api<Summary>(`${prefix}/summary?only_available=true`, { signal }),
      chart
        ? api<CapacityHistory>(
            `${prefix}/availability-history?from=${encodeURIComponent(from.toISOString())}&to=${encodeURIComponent(now.toISOString())}`,
            { signal },
          )
        : Promise.resolve(null),
    ]);
    return { summary, history };
  }, `${pool.kind}/${pool.id}/${chart}`, chart, revision);
  return (
    <section
      className="pool-card"
      aria-label={pool.name}
      aria-busy={chart && state.loading && state.data ? true : undefined}
    >
      <header className="pool-card-header">
        <div className="pool-title">
          <span className="pool-icon">
            <Icon name={pool.kind === "users" ? "computer" : "users"} />
          </span>
          <div>
            <p className="eyebrow">
              {t(pool.kind === "users" ? "personalType" : "organizationType")}
            </p>
            <h2>{pool.name}</h2>
          </div>
        </div>
        <Link
          className="text-link"
          to={`/compute/computers?${pool.kind === "users" ? "user_id" : "company_id"}=${pool.id}`}
        >
          {t("viewComputers")} <Icon name="arrow" size={16} />
        </Link>
      </header>
      {state.loading && !state.data ? (
        <Loading />
      ) : state.error ? (
        <ErrorBox error={state.error} retry={state.reload} />
      ) : (
        state.data && (
          <>
            <Metrics summary={state.data.summary} compact={chart} />
            {state.data.history && (
              <CapacityChart history={state.data.history} />
            )}
          </>
        )
      )}
    </section>
  );
}
