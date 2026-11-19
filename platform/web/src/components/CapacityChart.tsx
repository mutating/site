import { Fragment, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  GiB,
  capacityPointIndex,
  capacitySeries,
  capacityTicks,
  capacityWindow,
  day,
  roundedStepPath,
  type CapacityField,
  type CapacityWindow,
} from "../lib/chart";
import { useI18n } from "../lib/i18n";
import type { CapacityHistory } from "../lib/types";
import { Icon } from "./UI";

const resources = [
  {
    code: "cpu",
    divisor: 1000,
    series: [
      { field: "cpu_millis", label: "cpuCapacity" },
      { field: "cpu_occupied_millis", label: "cpuUsage" },
    ],
  },
  {
    code: "memory",
    divisor: GiB,
    series: [
      { field: "memory_bytes", label: "memoryCapacity" },
      { field: "memory_occupied_bytes", label: "memoryUsage" },
    ],
  },
] as const;

const shortDate = { day: "numeric", month: "short" } as const;
const plotTop = 10;
const minute = 60_000;

function clockOptions(interval: number): Intl.DateTimeFormatOptions {
  return {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    ...(interval < minute && { second: "2-digit" }),
    ...(interval < 1000 && { fractionalSecondDigits: 3 }),
  };
}

function timeTicks({ start, end }: CapacityWindow, width: number, locale: string) {
  const span = end - start;
  const dateOnly = span >= 2 * day;
  const dateWithClock = span >= day && !dateOnly;
  const spacing = dateWithClock ? 128 : span < 6000 ? 108 : 82;
  const limit = dateOnly ? Math.min(6, Math.floor(span / day)) : 6;
  const divisions = Math.min(limit, Math.max(1, Math.floor(width / spacing)));
  const interval = span / divisions;
  const format = new Intl.DateTimeFormat(
    locale,
    dateOnly
      ? shortDate
      : {
          ...(dateWithClock && { month: "numeric", day: "numeric" }),
          ...clockOptions(interval),
        },
  );
  const endpointWidth = (format.format(start).length + format.format(end).length) * 6 + 8;
  if (!span || width < endpointWidth) {
    const time = start + span / 2;
    return [{ time, label: format.format(time) }];
  }

  const labels = new Set<string>();
  return Array.from({ length: divisions + 1 }, (_, index) => {
    const time = index === divisions ? end : start + interval * index;
    const label = format.format(time);
    return { time, label };
  }).filter(({ label }) => {
    if (labels.has(label)) return false;
    labels.add(label);
    return true;
  });
}

function plotGeometry(range: CapacityWindow, width: number, locale: string) {
  const { start, end, points } = range;
  const height = width < 375 ? 158 : width < 640 ? 175 : 206;
  const compact = new Intl.NumberFormat(locale, {
    notation: "compact",
    maximumSignificantDigits: 3,
  });
  const scientific = new Intl.NumberFormat(locale, {
    notation: "scientific",
    maximumSignificantDigits: 2,
  });
  const axes = resources.map((resource) => {
    const { series, divisor } = resource;
    const peak = points.reduce(
      (max, point) => Math.max(max, point[series[0].field], point[series[1].field]),
      0,
    ) / divisor;
    const ticks = capacityTicks(peak);
    const maximum = ticks.at(-1)! * divisor;
    const labels = ticks.map((value) =>
      (value > 0 && value < 0.0001 ? scientific : compact).format(value),
    );
    return {
      resource,
      labels,
      inset: Math.max(26, 10 + Math.max(...labels.map((label) => label.length)) * 6.5),
      y: (value: number) => height * (1 - value / maximum),
    };
  });

  const left = axes[0].inset;
  const plotWidth = Math.max(1, width - left - axes[1].inset);
  const x = (time: number) =>
    end > start ? ((time - start) / (end - start)) * plotWidth : plotWidth / 2;
  const columns = Math.max(1, Math.floor(plotWidth / 14));
  const lines = axes.flatMap(({ resource, y }) =>
    resource.series.map(({ field }, index) => {
      const { samples, bands } = capacitySeries(points, field, columns);
      return {
        code: resource.code,
        field,
        reserved: index === 1,
        path: roundedStepPath(samples, x, y),
        bands,
        y,
      };
    }),
  );
  // Both fills sit below the traces. Reservations stay above both capacity lines.
  lines.sort((a, b) => Number(a.reserved) - Number(b.reserved));

  return {
    axes,
    left,
    plotWidth,
    height,
    x,
    lines,
    timeLabels: timeTicks(range, plotWidth, locale),
    date: new Intl.DateTimeFormat(locale, {
      ...shortDate,
      ...clockOptions(Math.min(minute - 1, end - start)),
    }),
    number: new Intl.NumberFormat(locale, { maximumFractionDigits: 3 }),
    smallNumber: new Intl.NumberFormat(locale, { maximumSignificantDigits: 3 }),
  };
}

function CapacityPlot({
  range,
  hidden,
  onToggle,
}: {
  range: CapacityWindow;
  hidden: CapacityField[];
  onToggle: (field: CapacityField) => void;
}) {
  const { t, locale } = useI18n();
  const gradient = useId();
  const aggregation = useId();
  const viewport = useRef<HTMLDivElement>(null);
  const keyboardReading = useRef(false);
  const lastPointer = useRef<{ x: number; y: number } | null>(null);
  const [width, setWidth] = useState(360);
  const [cursor, setCursor] = useState<number | null>(null);
  useLayoutEffect(() => {
    const element = viewport.current!;
    setWidth(element.clientWidth);
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.floor(entry.contentRect.width)),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const plot = useMemo(() => plotGeometry(range, width, locale), [range, width, locale]);
  const { start, end, points } = range;
  const readingTime = Math.max(start, Math.min(cursor ?? end, end));
  const index = capacityPointIndex(points, readingTime);
  const sample = points[index];
  const dateLabel = plot.date.format(readingTime);
  const visible = plot.lines.filter(({ field }) => !hidden.includes(field));
  const readings = resources.flatMap(({ code, divisor, series }) =>
    series.flatMap(({ field, label }) => {
      if (hidden.includes(field)) return [];
      const value = sample[field] / divisor;
      const format = value > 0 && value < 0.001 ? plot.smallNumber : plot.number;
      return [{
        field,
        label: t(label),
        value: format.format(value),
        unit: code === "cpu" ? t("cores") : "GiB",
      }];
    }),
  );
  const inspectable = visible.length > 0;
  const readingOpen = inspectable && cursor !== null;
  const cursorX = plot.x(readingTime);
  const tooltipWidth = Math.min(216, width * 0.8);
  const tooltipSide = cursorX < plot.plotWidth / 2 ? 12 : -tooltipWidth - 12;
  const tooltipLeft = Math.max(0, Math.min(width - tooltipWidth, plot.left + cursorX + tooltipSide));
  const tooltipColumns = [0, 1].filter((i) =>
    resources.some(({ series }) => !hidden.includes(series[i].field)),
  );
  const showsBands = visible.some(({ bands }) => bands.length > 0);
  const grid = plot.axes[0].labels.map((_, i, labels) =>
    plot.height * (1 - i / (labels.length - 1)),
  );

  useEffect(() => {
    if (!readingOpen) return;
    const plotElement = viewport.current;
    const dismissOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !plotElement?.contains(event.target)) {
        setCursor(null);
      }
    };
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setCursor(null);
    };
    document.addEventListener("pointerdown", dismissOutside, true);
    document.addEventListener("keydown", dismissOnEscape, true);
    return () => {
      document.removeEventListener("pointerdown", dismissOutside, true);
      document.removeEventListener("keydown", dismissOnEscape, true);
    };
  }, [readingOpen]);

  const selectTime = (clientX: number) => {
    if (!inspectable) return;
    const fromLeft = clientX - viewport.current!.getBoundingClientRect().left - plot.left;
    const fraction = Math.max(0, Math.min(1, fromLeft / plot.plotWidth));
    setCursor(start + (end - start) * fraction);
  };

  return (
    <div className="chart-content">
      <div className="chart-legend">
        {resources.map(({ code, series }) => (
          <div key={code} className={`chart-legend-group chart-series--${code}`}>
            <span>{code === "cpu" ? "CPU" : t("memory")}</span>
            <div className="chart-legend-buttons">
              {series.map(({ field, label }, seriesIndex) => (
                <button
                  key={field}
                  type="button"
                  aria-label={t(label)}
                  aria-pressed={!hidden.includes(field)}
                  onClick={() => {
                    setCursor(null);
                    onToggle(field);
                  }}
                >
                  <span className={`chart-swatch${seriesIndex ? " chart-swatch--reserved" : ""}`} />
                  {t(seriesIndex ? "chartReserved" : "chartCapacity")}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="chart-axis-captions" aria-hidden="true">
        <span className="chart-series--cpu">CPU <small>{t("cores")}</small></span>
        <span className="chart-series--memory">{t("memory")} <small>GiB</small></span>
      </div>
      <div
        className={inspectable ? "chart-viewport" : "chart-viewport chart-viewport--hidden"}
        ref={viewport}
        role={inspectable ? "slider" : undefined}
        tabIndex={inspectable ? 0 : undefined}
        aria-label={inspectable ? t("history") : undefined}
        aria-describedby={showsBands ? aggregation : undefined}
        aria-valuemin={inspectable ? 0 : undefined}
        aria-valuemax={inspectable ? points.length - 1 : undefined}
        aria-valuenow={inspectable ? index : undefined}
        aria-valuetext={inspectable
          ? [dateLabel, ...readings.map(({ label, value, unit }) => `${label} ${value} ${unit}`)].join(" · ")
          : undefined}
        onFocus={(event) => {
          if (event.currentTarget.matches(":focus-visible")) {
            keyboardReading.current = true;
            setCursor((previous) => previous ?? end);
          }
        }}
        onBlur={() => {
          keyboardReading.current = false;
          setCursor(null);
        }}
        onPointerMove={(event) => {
          if (event.pointerType !== "mouse") return;
          const { clientX: x, clientY: y } = event;
          const previous = lastPointer.current;
          lastPointer.current = { x, y };
          // A layout or scroll change can dispatch a move without moving the pointer.
          if (!keyboardReading.current && (previous?.x !== x || previous?.y !== y)) {
            selectTime(x);
          }
        }}
        onPointerLeave={(event) => {
          if (event.pointerType === "mouse" && !keyboardReading.current) setCursor(null);
        }}
        onClick={(event) => {
          keyboardReading.current = false;
          selectTime(event.clientX);
        }}
        onKeyDown={(event) => {
          let next: number;
          switch (event.key) {
            case "ArrowLeft":
            case "ArrowDown":
              next = cursor !== null && readingTime > sample.time ? index : Math.max(0, index - 1);
              break;
            case "ArrowRight":
            case "ArrowUp":
              next = Math.min(points.length - 1, index + 1);
              break;
            case "Home":
              next = 0;
              break;
            case "End":
              next = points.length - 1;
              break;
            default:
              return;
          }
          event.preventDefault();
          keyboardReading.current = true;
          setCursor(points[next].time);
        }}
      >
        <svg
          className="capacity-chart"
          viewBox={`0 0 ${width} ${plotTop + plot.height + 25}`}
          aria-hidden="true"
        >
          <defs>
            {resources.map(({ code }) => (
              <linearGradient
                key={code}
                id={`${gradient}-${code}`}
                className={`chart-series--${code}`}
                x1="0"
                x2="0"
                y1="0"
                y2={plot.height}
                gradientUnits="userSpaceOnUse"
              >
                <stop offset="0%" className="chart-fill-top" />
                <stop offset="100%" className="chart-fill-bottom" />
              </linearGradient>
            ))}
          </defs>
          <g transform={`translate(${plot.left} ${plotTop})`}>
            {grid.map((y, i) => (
              <line key={y} x1="0" x2={plot.plotWidth} y1={y} y2={y} className={i ? "chart-grid" : "chart-baseline"} />
            ))}
            {plot.axes.map(({ resource: { code }, labels }, axisIndex) => (
              <g
                key={code}
                data-axis={code}
                className={`chart-axis chart-series--${code}${visible.some((line) => line.code === code) ? "" : " chart-axis--inactive"}`}
              >
                {labels.map((label, i) => (
                  <text
                    key={i}
                    x={axisIndex ? plot.plotWidth + 7 : -7}
                    y={grid[i]}
                    dy="0.34em"
                    textAnchor={axisIndex ? "start" : "end"}
                    className="chart-tick"
                  >
                    {label}
                  </text>
                ))}
              </g>
            ))}
            {plot.timeLabels.map(({ time, label }) => (
              <text
                key={time}
                x={plot.x(time)}
                y={plot.height + 20}
                textAnchor={start === end ? "middle" : time === start ? "start" : time === end ? "end" : "middle"}
                className="chart-tick"
              >
                {label}
              </text>
            ))}
            {visible.map(({ code, field, path, reserved }) => !reserved && path && (
              <path key={field} data-fill={code} d={`${path}V${plot.height}H0Z`} fill={`url(#${gradient}-${code})`} />
            ))}
            {visible.map(({ code, field, bands, reserved, y }) => bands.length > 0 && (
              <g key={field} className={`chart-band chart-series--${code}${reserved ? " chart-band--reserved" : ""}`}>
                {bands.map(({ left, right, low, high }) => (
                  <rect
                    key={left}
                    x={plot.x(left)}
                    y={y(high)}
                    width={plot.x(right) - plot.x(left)}
                    height={y(low) - y(high)}
                  />
                ))}
              </g>
            ))}
            {visible.map(({ code, field, path, reserved }) => path && (
              <path
                key={field}
                data-series={field}
                d={path}
                className={`chart-line chart-series--${code}${reserved ? " chart-line--reserved" : ""}`}
              />
            ))}
            {readingOpen && (
              <line x1={cursorX} x2={cursorX} y1="0" y2={plot.height} className="chart-cursor" />
            )}
            {(readingOpen || points.length === 1) && visible.map(({ code, field, reserved, y }) => (
              <circle
                key={field}
                cx={cursorX}
                cy={y(sample[field])}
                className={`chart-marker chart-series--${code}${reserved ? " chart-marker--reserved" : ""}`}
              />
            ))}
          </g>
        </svg>
        {readingOpen && (
          <div
            className="chart-tooltip"
            aria-hidden="true"
            style={{ left: tooltipLeft, width: tooltipWidth }}
          >
            <time dateTime={new Date(readingTime).toISOString()}>{dateLabel}</time>
            <div
              className="chart-tooltip-values"
              style={{ gridTemplateColumns: `auto repeat(${tooltipColumns.length}, minmax(0, 1fr))` }}
            >
              <span />
              {tooltipColumns.map((i) => (
                <span key={i}>{t(i ? "chartReserved" : "chartCapacity")}</span>
              ))}
              {resources.map(({ code, series }) => {
                if (series.every(({ field }) => hidden.includes(field))) return null;
                return (
                  <Fragment key={code}>
                    <span className={`chart-tooltip-resource chart-series--${code}`}>
                      {code === "cpu" ? "CPU" : t("memory")}
                    </span>
                    {tooltipColumns.map((i) => {
                      const reading = readings.find(({ field }) => field === series[i].field);
                      return <strong key={i} data-reading={reading?.field}>{reading?.value}</strong>;
                    })}
                  </Fragment>
                );
              })}
            </div>
          </div>
        )}
        {!inspectable && <p className="chart-lines-hidden">{t("chartLinesHidden")}</p>}
      </div>
      {showsBands && <p id={aggregation} className="chart-aggregation">{t("chartAggregated")}</p>}
    </div>
  );
}

export function CapacityChart({ history }: { history: CapacityHistory }) {
  const { t, locale } = useI18n();
  const heading = useId();
  const [hidden, setHidden] = useState<CapacityField[]>([]);
  const toggle = (field: CapacityField) => {
    setHidden((previous) => previous.includes(field)
      ? previous.filter((item) => item !== field)
      : [...previous, field]);
  };
  const range = useMemo(() => capacityWindow(history), [history]);
  const available = Date.parse(history.available_from);
  const partial = available > Date.parse(history.from_time);
  const span = range ? range.end - range.start : 0;
  const date = new Intl.DateTimeFormat(locale, shortDate);
  const period = new Intl.DateTimeFormat(locale, {
    ...shortDate,
    ...(span < 2 * day && clockOptions(span)),
  });

  return (
    <section className="chart-panel" aria-labelledby={heading}>
      <header className="chart-header">
        <h3 id={heading}>{t("history")}</h3>
        {range && <span className="chart-period">{period.formatRange(range.start, range.end)}</span>}
      </header>
      {range?.hasCapacity ? (
        <CapacityPlot range={range} hidden={hidden} onToggle={toggle} />
      ) : (
        <div className="chart-empty">
          <Icon name="compute" size={20} />
          <p>{t(range ? "noCapacityHistory" : "noHistory")}</p>
        </div>
      )}
      {partial && (
        <p className="chart-note">{t("historyFrom")} {date.format(available)}</p>
      )}
    </section>
  );
}
