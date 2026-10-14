import { useEffect, useId, useRef, useState } from "react";
import type { CapacityHistory, CapacityPoint } from "../lib/types";
import { GiB, number, stepPath } from "../lib/chart";
import { useI18n, type MessageKey } from "../lib/i18n";
const series: {
  field: keyof Omit<CapacityPoint, "at">;
  label: MessageKey;
  color: string;
  dashed?: boolean;
  divisor: number;
}[] = [
  {
    field: "cpu_millis",
    label: "cpuCapacity",
    color: "var(--hot)",
    divisor: 1000,
  },
  {
    field: "cpu_occupied_millis",
    label: "cpuUsage",
    color: "var(--hot-soft)",
    dashed: true,
    divisor: 1000,
  },
  {
    field: "memory_bytes",
    label: "memoryCapacity",
    color: "var(--violet)",
    divisor: GiB,
  },
  {
    field: "memory_occupied_bytes",
    label: "memoryUsage",
    color: "var(--violet-soft)",
    dashed: true,
    divisor: GiB,
  },
];
export function CapacityChart({ history }: { history: CapacityHistory }) {
  const { t, locale } = useI18n();
  const id = useId();
  const [hidden, setHidden] = useState<string[]>([]);
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(900);
  useEffect(() => {
    const observer = new ResizeObserver((entries) =>
      setWidth(Math.max(280, Math.min(900, entries[0].contentRect.width))),
    );
    if (ref.current) observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  const plotWidth = width - 100;
  const height = width < 500 ? 200 : 240;
  const divisions = width < 500 ? 3 : 7;
  const niceMax = (value: number) => {
    const magnitude = 10 ** Math.floor(Math.log10(value));
    const normalized = value / magnitude;
    return (
      magnitude *
      (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10)
    );
  };
  const start = Date.parse(history.from_time),
    end = Date.parse(history.to_time);
  const cpuMax =
    niceMax(
      history.points.reduce(
        (max, p) => Math.max(max, p.cpu_millis, p.cpu_occupied_millis),
        1000,
      ) / 1000,
    ) * 1000;
  const ramMax =
    niceMax(
      history.points.reduce(
        (max, p) => Math.max(max, p.memory_bytes, p.memory_occupied_bytes),
        GiB,
      ) / GiB,
    ) * GiB;
  const date = (at: number) =>
    new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" }).format(
      at,
    );
  return (
    <div className="chart-panel" ref={ref}>
      <div className="chart-header">
        <span>{t("history")}</span>
        <span>{t("sevenDays")}</span>
      </div>
      <div className="chart-legend">
        {series.map((s) => (
          <button
            key={s.field}
            type="button"
            aria-pressed={!hidden.includes(s.field)}
            className={
              hidden.includes(s.field) ? "legend-item dimmed" : "legend-item"
            }
            onClick={() =>
              setHidden((x) =>
                x.includes(s.field)
                  ? x.filter((f) => f !== s.field)
                  : [...x, s.field],
              )
            }
          >
            <span
              style={{
                borderColor: s.color,
                borderTopStyle: s.dashed ? "dashed" : "solid",
              }}
            />
            {t(s.label)}
          </button>
        ))}
      </div>
      {history.points.length ? (
        <svg
          className="capacity-chart"
          viewBox={`0 0 ${width} ${height + 90}`}
          role="img"
          aria-labelledby={`${id}-title ${id}-description`}
        >
          <title id={`${id}-title`}>
            {t("history")} · {t("sevenDays")}
          </title>
          <desc id={`${id}-description`}>{t("chartNote")}</desc>
          <text x="50" y="16" className="axis-label">
            CPU · {t("cores")}
          </text>
          <text x={width - 50} y="16" textAnchor="end" className="axis-label">
            {t("memory")} · GiB
          </text>
          <g transform="translate(50 40)">
            {[0, 1, 2, 3, 4].map((i) => (
              <g key={i}>
                <line
                  x1="0"
                  x2={plotWidth}
                  y1={(i * height) / 4}
                  y2={(i * height) / 4}
                  className="chart-grid"
                />
                <text
                  x="-10"
                  y={(i * height) / 4 + 4}
                  textAnchor="end"
                  className="tick"
                >
                  {number((cpuMax / 1000) * (1 - i / 4), locale)}
                </text>
                <text
                  x={plotWidth + 10}
                  y={(i * height) / 4 + 4}
                  className="tick"
                >
                  {number((ramMax / GiB) * (1 - i / 4), locale)}
                </text>
              </g>
            ))}
            {Array.from({ length: divisions + 1 }, (_, i) => i).map((i) => (
              <text
                key={i}
                x={(i * plotWidth) / divisions}
                y={height + 32}
                textAnchor={
                  i === 0 ? "start" : i === divisions ? "end" : "middle"
                }
                className="tick"
              >
                {date(start + ((end - start) * i) / divisions)}
              </text>
            ))}
            {series
              .filter((s) => !hidden.includes(s.field))
              .map((s) => (
                <path
                  key={s.field}
                  data-series={s.field}
                  d={stepPath(
                    history.points,
                    s.field,
                    start,
                    end,
                    s.divisor === 1000 ? cpuMax : ramMax,
                    plotWidth,
                    height,
                  )}
                  fill="none"
                  stroke={s.color}
                  strokeWidth="2.5"
                  strokeDasharray={s.dashed ? "6 5" : undefined}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
          </g>
        </svg>
      ) : (
        <p className="chart-empty">{t("noHistory")}</p>
      )}
      <p className="chart-note">{t("chartNote")}</p>
      {Date.parse(history.available_from) > start && (
        <p className="chart-note">
          {t("historyFrom")} {date(Date.parse(history.available_from))}.
        </p>
      )}
      {!!history.points.length && (
        <details className="chart-data">
          <summary>{t("chartTable")}</summary>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>{t("time")}</th>
                  {series.map((s) => (
                    <th key={s.field}>
                      {t(s.label)} · {s.divisor === 1000 ? t("cores") : "GiB"}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.points.map((p, i) => (
                  <tr key={i}>
                    <td>{new Date(p.at).toLocaleString(locale)}</td>
                    {series.map((s) => (
                      <td key={s.field}>
                        {number(p[s.field] / s.divisor, locale)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  );
}
