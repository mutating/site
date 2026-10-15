import { useEffect, useRef } from "react";
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
  const [params] = useSearchParams();
  const selected = params.get("benchmark");
  const state = useData(
    (signal) =>
      api<BenchmarkResult[]>(
        `computers/${computerId}/benchmark-results?latest_only=true`,
        { signal },
      ),
    computerId,
  );
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
          computerId={computerId}
          name={selected}
        />
      )}
    </>
  );
}

function BenchmarkHistory({
  computerId,
  name,
}: {
  computerId: string;
  name: string;
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
  const state = useData(async (signal) => {
    const results = await api<BenchmarkResult[]>(
      `computers/${computerId}/benchmark-results?latest_only=false`,
      { signal },
    );
    // The API orders by SemVer, including prereleases and its build metadata tie-break.
    return results.filter((result) => result.benchmark_name === name).reverse();
  }, computerId);
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
      {state.loading ? (
        <Loading />
      ) : state.error ? (
        <ErrorBox error={state.error} retry={state.reload} />
      ) : !state.data?.length ? (
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
              {state.data.map((result) => (
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
