import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { useData } from "../lib/useData";
import { useI18n } from "../lib/i18n";
import { GiB, number } from "../lib/chart";
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
export function Metrics({ summary }: { summary: Summary }) {
  const { t, locale } = useI18n();
  const cpu = summary.resources.find((r) => r.code === "cpu");
  const memory = summary.resources.find((r) => r.code === "memory");
  return (
    <div className="metrics">
      <div className="metric">
        <span className="metric-label">
          <Icon name="cpu" size={17} />
          CPU
        </span>
        <div className="metric-value">
          <span className="metric-free">
            {number((cpu?.free || 0) / 1000, locale)}
          </span>
          <span className="metric-total">
            / {number((cpu?.total || 0) / 1000, locale)}
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
            {number((memory?.free || 0) / GiB, locale)}
          </span>
          <span className="metric-total">
            / {number((memory?.total || 0) / GiB, locale)}
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
          {number(summary.computer_count, locale)}
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
    const from = new Date(now.getTime() - 7 * 86400000);
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
  }, `${pool.kind}/${pool.id}/${chart}/${revision}`);
  return (
    <section className="pool-card" aria-label={pool.name}>
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
          to={`/power-cells/computers?${pool.kind === "users" ? "user_id" : "company_id"}=${pool.id}`}
        >
          {t("viewComputers")} <Icon name="arrow" size={16} />
        </Link>
      </header>
      {state.loading ? (
        <Loading />
      ) : state.error ? (
        <ErrorBox error={state.error} retry={state.reload} />
      ) : (
        state.data && (
          <>
            <Metrics summary={state.data.summary} />
            {state.data.history && (
              <CapacityChart history={state.data.history} />
            )}
          </>
        )
      )}
    </section>
  );
}
