import { useEffect, useRef, useState, type RefObject } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { useData } from "../lib/useData";
import { useI18n } from "../lib/i18n";
import type { BenchmarkResult } from "../lib/types";
import { Empty, ErrorBox, Loading } from "./UI";

function score(value: number, locale: string) {
  return new Intl.NumberFormat(locale, { maximumSignificantDigits: 21 }).format(
    value,
  );
}

export function BenchmarkSummary({
  computerId,
  to,
}: {
  computerId: string;
  to: string;
}) {
  const { t, locale } = useI18n();
  const state = useData(
    (signal) =>
      api<BenchmarkResult[]>(
        `computers/${computerId}/benchmark-results?latest_only=true`,
        { signal },
      ),
    computerId,
  );
  return (
    <div className="benchmark-summary">
      {state.loading ? (
        <Loading />
      ) : state.error ? (
        <ErrorBox error={state.error} retry={state.reload} />
      ) : !state.data?.length ? (
        <span className="muted">{t("noBenchmarks")}</span>
      ) : (
        <>
          <small title={state.data[0].benchmarker_version}>
            {t("benchmarkVersion")} · {state.data[0].benchmarker_version}
          </small>
          <dl>
            {state.data.slice(0, 2).map((result) => (
              <div key={result.id}>
                <dt title={result.benchmark_name}>{result.benchmark_name}</dt>
                <dd>{score(result.number, locale)}</dd>
              </div>
            ))}
          </dl>
          {state.data.length > 2 && (
            <Link className="text-button" to={to}>
              +{state.data.length - 2} {t("moreBenchmarks")}
            </Link>
          )}
        </>
      )}
    </div>
  );
}

export function Benchmarks({ computerId, interrupted }: {
  computerId: string;
  interrupted: RefObject<boolean>;
}) {
  const { t, locale } = useI18n();
  const [params] = useSearchParams();
  const selected = params.get("benchmark");
  const [historyRequested, setHistoryRequested] = useState(!!selected);
  const needHistory = historyRequested || !!selected;
  useEffect(() => {
    if (selected) setHistoryRequested(true);
  }, [selected]);
  const state = useData(
    (signal) =>
      api<BenchmarkResult[]>(
        `computers/${computerId}/benchmark-results?latest_only=true`,
        { signal },
      ),
    computerId,
  );
  const history = useData(
    (signal) =>
      needHistory
        ? api<BenchmarkResult[]>(
            `computers/${computerId}/benchmark-results?latest_only=false`,
            { signal },
          )
        : Promise.resolve(null),
    `${computerId}:${needHistory}`,
  );
  const historyLoading = history.loading || (!history.data && !history.error);
  return (
    <>
      <section
        className="panel benchmark-panel"
        aria-labelledby="latest-benchmarks-title"
      >
        <h2 id="latest-benchmarks-title">{t("latestBenchmarks")}</h2>
        <p className="muted section-lede">{t("latestBenchmarksLead")}</p>
        {state.loading ? (
          <Loading />
        ) : state.error ? (
          <ErrorBox error={state.error} retry={state.reload} />
        ) : !state.data?.length ? (
          <Empty icon="cpu" title={t("noBenchmarks")}>
            {t("noBenchmarksLead")}
          </Empty>
        ) : (
          <>
            <p className="eyebrow benchmark-version">
              {t("benchmarkVersion")} · {state.data[0].benchmarker_version}
            </p>
            <div className="benchmark-grid">
              {state.data.map((result) => {
                const next = new URLSearchParams(params);
                next.set("benchmark", result.benchmark_name);
                return (
                  <Link
                    key={result.id}
                    className="benchmark-card"
                    to={`?${next}`}
                    aria-current={
                      selected === result.benchmark_name ? "true" : undefined
                    }
                  >
                    <span>{result.benchmark_name}</span>
                    <strong>{score(result.number, locale)}</strong>
                    <small>{t("benchmarkHistory")} →</small>
                  </Link>
                );
              })}
            </div>
          </>
        )}
      </section>
      {selected && (
        <BenchmarkHistory
          key={`${computerId}:${selected}`}
          name={selected}
          results={history.data}
          loading={historyLoading}
          latestLoading={state.loading}
          interrupted={interrupted}
          error={history.error}
          reload={history.reload}
        />
      )}
    </>
  );
}

function BenchmarkHistory({
  name,
  results,
  loading,
  latestLoading,
  interrupted,
  error,
  reload,
}: {
  name: string;
  results: BenchmarkResult[] | null;
  loading: boolean;
  latestLoading: boolean;
  interrupted: RefObject<boolean>;
  error: unknown;
  reload: () => void;
}) {
  const { t, locale } = useI18n();
  const { key: navigationKey, hash } = useLocation();
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    if (hash) return;
    const active = document.activeElement;
    const frame = requestAnimationFrame(() => {
      if (
        interrupted.current ||
        (document.activeElement !== active && document.activeElement !== document.body)
      ) return;
      section.current?.scrollIntoView({ block: "start" });
      section.current?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [navigationKey, hash, interrupted]);
  useEffect(() => {
    if (hash) return;
    // Layout can change scrollY without user input. Follow this navigation until
    // a gesture interrupts it or focus moves elsewhere.
    const frame = requestAnimationFrame(() => {
      if (
        !interrupted.current &&
        section.current?.contains(document.activeElement)
      ) {
        section.current.scrollIntoView({ block: "start" });
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [loading, latestLoading, hash, interrupted]);
  const date = new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "medium",
  });
  // The API returns canonical UTC dates. Without the trailing Z, whole seconds
  // sort before their fractions, preserving precision beyond JavaScript's Date.
  const rows = results
    ?.filter((result) => result.benchmark_name === name)
    .sort(
      (a, b) =>
        b.created_at.replace(/Z$/, "").localeCompare(a.created_at.replace(/Z$/, "")) ||
        b.run_id.localeCompare(a.run_id),
    );
  return (
    <section
      ref={section}
      tabIndex={-1}
      className="panel benchmark-panel"
      aria-labelledby="benchmark-history-title"
    >
      <h2 id="benchmark-history-title">
        {t("benchmarkHistory")} · {name}
      </h2>
      <p className="muted section-lede">{t("benchmarkHistoryOrder")}</p>
      {loading ? (
        <Loading />
      ) : error ? (
        <ErrorBox error={error} retry={reload} />
      ) : !rows?.length ? (
        <p className="muted">{t("noBenchmarkHistory")}</p>
      ) : (
        <div
          className="table-scroll"
          tabIndex={0}
          role="group"
          aria-labelledby="benchmark-history-title"
          onKeyDown={event => {
            if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
                !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
            const table = event.currentTarget;
            if (table.scrollWidth > table.clientWidth) {
              event.preventDefault();
              table.scrollBy(event.key === "ArrowRight" ? 80 : -80, 0);
            }
          }}
        >
          <table className="benchmark-history-table">
            <thead>
              <tr>
                <th scope="col" aria-sort="descending">
                  {t("benchmarkDate")} ↓
                </th>
                <th scope="col">{t("benchmarkVersion")}</th>
                <th scope="col">{t("benchmarkScore")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((result) => (
                <tr key={result.id}>
                  <th scope="row">
                    <time dateTime={result.created_at}>
                      {date.format(new Date(result.created_at))}
                    </time>
                  </th>
                  <td className="benchmark-tooling-version">
                    {result.benchmarker_version}
                  </td>
                  <td className="numeric">{score(result.number, locale)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
