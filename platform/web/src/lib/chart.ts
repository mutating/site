import type { CapacityHistory, CapacityPoint } from "./types";

export const GiB = 1024 ** 3;
export const hour = 60 * 60 * 1000;
export const day = 24 * hour;

export function number(value: number, locale: string) {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(
    value,
  );
}

export type CapacityField = keyof Omit<CapacityPoint, "at">;
export type TimedCapacityPoint = Omit<CapacityPoint, "at"> & { time: number };
type TimedValue = { time: number; value: number };

export interface CapacityWindow {
  start: number;
  end: number;
  points: TimedCapacityPoint[];
  hasCapacity: boolean;
}

// The API timestamps events to the microsecond. Date.parse keeps whole milliseconds.
function parseTime(at: string) {
  const remainder = /\.\d{3}(\d+)/.exec(at)?.[1];
  return Date.parse(at) + (remainder ? Number(`0.${remainder}`) : 0);
}

export function capacityPointIndex(points: TimedCapacityPoint[], time: number) {
  let left = 0;
  let right = points.length;
  while (left < right) {
    const middle = Math.floor((left + right) / 2);
    if (points[middle].time <= time) left = middle + 1;
    else right = middle;
  }
  return Math.max(0, left - 1);
}

export function capacityWindow(history: CapacityHistory): CapacityWindow | null {
  const beginning = Math.max(parseTime(history.from_time), parseTime(history.available_from));
  const ending = Math.min(parseTime(history.to_time), parseTime(history.as_of));
  const points: TimedCapacityPoint[] = [];
  for (const { at, ...values } of history.points) {
    const time = parseTime(at);
    if (time < beginning || time > ending) continue;
    const point = { time, ...values };
    if (points.at(-1)?.time === time) points[points.length - 1] = point;
    else points.push(point);
  }
  if (!points.length) return null;

  const first = points[0].time;
  const last = points.at(-1)!.time;
  const idleThreshold = Math.min(day, (last - first) / 8);
  let activeStart: number | undefined;
  let activeEnd = first;
  let earliest = first;
  points.forEach((point, i) => {
    const { time, cpu_millis, memory_bytes, cpu_occupied_millis, memory_occupied_bytes } = point;
    if (!(cpu_millis || memory_bytes || cpu_occupied_millis || memory_occupied_bytes)) return;
    const gap = time - activeEnd;
    if (activeStart === undefined) activeStart = time;
    else if (gap > 0 && gap >= idleThreshold) {
      activeStart = time;
      earliest = activeEnd + gap / 2;
    }
    activeEnd = points[i + 1]?.time ?? time;
  });
  if (activeStart === undefined) {
    return { start: first, end: last, points, hasCapacity: false };
  }

  // Keep short idle gaps and a little context around the latest active period.
  const padding = activeEnd > activeStart ? Math.max(0.001, (activeEnd - activeStart) * 0.12) : 0;
  const start = Math.max(earliest, activeStart - padding);
  const end = Math.min(last, activeEnd + padding);
  const startIndex = capacityPointIndex(points, start);
  const endIndex = capacityPointIndex(points, end);
  const visible = [
    { ...points[startIndex], time: start },
    ...points.slice(startIndex + 1, endIndex + 1),
  ];
  if (visible.at(-1)!.time < end) visible.push({ ...points[endIndex], time: end });
  return { start, end, points: visible, hasCapacity: true };
}

export function capacityTicks(peak: number) {
  const divisions = 4;
  const target = (peak || 1) / divisions;
  const magnitude = 10 ** Math.floor(Math.log10(target));
  const steps = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  const step = steps.find((value) => value * magnitude >= target)! * magnitude;
  return Array.from({ length: divisions + 1 }, (_, index) => index * step);
}

export function capacitySeries(points: TimedCapacityPoint[], field: CapacityField, columns: number) {
  const samples: TimedValue[] = [];
  for (const [index, point] of points.entries()) {
    const value = point[field];
    if (value !== samples.at(-1)?.value || index === points.length - 1) {
      samples.push({ time: point.time, value });
    }
  }
  if (samples.length <= columns * 3) return { samples, bands: [] };

  const start = samples[0].time;
  const end = samples.at(-1)!.time;
  const duration = (end - start) / columns;
  const means: TimedValue[] = [];
  const bands: { left: number; right: number; low: number; high: number }[] = [];
  let index = 0;
  for (let column = 0; column < columns; column++) {
    const left = start + column * duration;
    const right = column === columns - 1 ? end : start + (column + 1) * duration;
    while (samples[index + 1]?.time <= left) index++;
    let { value } = samples[index];
    let low = value;
    let high = value;
    let sum = 0;
    let at = left;
    while (samples[index + 1]?.time < right) {
      const next = samples[++index];
      sum += value * (next.time - at);
      at = next.time;
      value = next.value;
      low = Math.min(low, value);
      high = Math.max(high, value);
    }
    sum += value * (right - at);
    means.push({ time: left, value: sum / (right - left) });
    if (high > low) bands.push({ left, right, low, high });
  }
  means.push({ time: end, value: means.at(-1)!.value });
  return { samples: means, bands };
}

export function roundedStepPath(
  points: TimedValue[],
  x: (time: number) => number,
  y: (value: number) => number,
) {
  if (points.length < 2) return "";
  const first = points[0];
  const endX = x(points.at(-1)!.time);
  const changes = points.filter((point, i) => i === 0 || point.value !== points[i - 1].value);
  const coordinate = (value: number) => value.toFixed(2);
  let path = `M${coordinate(x(first.time))},${coordinate(y(first.value))}`;
  for (let i = 1; i < changes.length; i++) {
    const [previous, point] = [changes[i - 1], changes[i]];
    const at = x(point.time);
    const fromY = y(previous.value);
    const toY = y(point.value);
    const nextX = changes[i + 1] ? x(changes[i + 1].time) : endX;
    const radius = Math.min(8, Math.abs(toY - fromY) / 2);
    // Fit each corner within its step. The middle of the change stays at the event time.
    const before = Math.min(radius, (at - x(previous.time)) / 2);
    const after = Math.min(radius, (nextX - at) / 2);
    const direction = Math.sign(toY - fromY);
    const corner = coordinate(at);
    path +=
      `H${coordinate(at - before)}` +
      `Q${corner},${coordinate(fromY)} ${corner},${coordinate(fromY + before * direction)}` +
      `V${coordinate(toY - after * direction)}` +
      `Q${corner},${coordinate(toY)} ${coordinate(at + after)},${coordinate(toY)}`;
  }
  return path + `H${coordinate(endX)}`;
}

export function stepPath<Field extends string>(
  points: readonly ({ at: string } & Record<Field, number>)[],
  field: Field,
  start: number,
  end: number,
  max: number,
  width = 800,
  height = 240,
) {
  if (!points.length || end <= start || max <= 0) return "";
  const x = (p: { at: string }) =>
    (((new Date(p.at).getTime() - start) / (end - start)) * width).toFixed(2);
  const y = (p: Record<Field, number>) =>
    (height - (Math.max(0, p[field]) / max) * height).toFixed(2);
  return points
    .map((p, i) => (i === 0 ? `M${x(p)},${y(p)}` : `H${x(p)}V${y(p)}`))
    .join("");
}
