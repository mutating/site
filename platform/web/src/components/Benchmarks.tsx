import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
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

export function Benchmarks({ computerId }: { computerId: string }) {
  const { t, locale } = useI18n();
  const [params, setParams] = useSearchParams();
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
  const names = [
    ...new Set(history.data?.map((result) => result.benchmark_name)),
  ].sort();
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
        <details
          className="benchmark-archive"
          onToggle={(event) => {
            if (event.currentTarget.open) setHistoryRequested(true);
          }}
        >
          <summary>{t("allBenchmarks")}</summary>
          {needHistory &&
            (historyLoading ? (
              <Loading />
            ) : history.error ? (
              <ErrorBox error={history.error} retry={history.reload} />
            ) : !names.length ? (
              <p className="muted">{t("noBenchmarks")}</p>
            ) : (
              <label className="field">
                {t("chooseBenchmark")}
                <select
                  value={names.includes(selected || "") ? selected! : ""}
                  onChange={(event) => {
                    const next = new URLSearchParams(params);
                    next.set("benchmark", event.target.value);
                    setParams(next);
                  }}
                >
                  <option value="" disabled>
                    {t("chooseBenchmark")}
                  </option>
                  {names.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            ))}
        </details>
      </section>
      {selected && (
        <BenchmarkHistory
          key={`${computerId}:${selected}`}
          name={selected}
          results={history.data}
          loading={historyLoading}
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
  error,
  reload,
}: {
  name: string;
  results: BenchmarkResult[] | null;
  loading: boolean;
  error: unknown;
  reload: () => void;
}) {
  const { t, locale } = useI18n();
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    // Run after the router's page reset so the selected history is visible on mobile.
    const frame = requestAnimationFrame(() => {
      section.current?.scrollIntoView({ block: "start" });
      section.current?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, []);
  // The API orders by SemVer, including prereleases and its build metadata tie-break.
  const rows = results
    ?.filter((result) => result.benchmark_name === name)
    .reverse();
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
        <div className="table-scroll">
          <table className="benchmark-history-table">
            <thead>
              <tr>
                <th scope="col" aria-sort="descending">
                  {t("benchmarkVersion")} ↓
                </th>
                <th scope="col">{t("benchmarkScore")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((result) => (
                <tr key={result.id}>
                  <th scope="row">{result.benchmarker_version}</th>
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
