import { describe, expect, it } from "vitest";
import {
  GiB,
  capacityPointIndex,
  capacitySeries,
  capacityTicks,
  capacityWindow,
  roundedStepPath,
} from "../../../../platform/web/src/lib/chart";
import type { CapacityHistory, CapacityPoint } from "../../../../platform/web/src/lib/types";

const point = (at: string, cpu = 0, memory = 0): CapacityPoint => ({
  at: `2026-01-${at}Z`,
  cpu_millis: cpu,
  memory_bytes: memory,
  cpu_occupied_millis: 0,
  memory_occupied_bytes: 0,
});

const history = (points: CapacityPoint[]): CapacityHistory => ({
  from_time: "2026-01-01T00:00:00Z",
  available_from: "2026-01-01T00:00:00Z",
  as_of: "2026-01-08T00:00:00Z",
  to_time: "2026-01-08T00:00:00Z",
  points,
});

describe("capacity history window", () => {
  it("focuses on the latest active period and keeps short interruptions in it", () => {
    const range = capacityWindow(history([
      point("01T00:00:00"),
      point("02T01:00:00", 90_000),
      point("02T01:30:00"),
      point("07T10:00:00", 5_000),
      point("07T10:04:00"),
      point("07T10:07:00", 0, 8 * GiB),
      point("07T10:10:00"),
      point("08T00:00:00"),
    ]))!;
    expect(new Date(range.start).toISOString()).toBe("2026-01-07T09:58:48.000Z");
    expect(new Date(range.end).toISOString()).toBe("2026-01-07T10:11:12.000Z");
    expect(range.points.map(({ cpu_millis, memory_bytes }) => [cpu_millis, memory_bytes]))
      .toEqual([[0, 0], [5_000, 0], [0, 0], [0, 8 * GiB], [0, 0], [0, 0]]);
  });

  it("covers the whole recorded range when capacity stays available", () => {
    const range = capacityWindow({
      ...history([
        point("02T00:00:00", 2000),
        point("05T00:00:00", 2000, GiB),
        point("07T00:00:00", 0, GiB),
      ]),
      available_from: "2026-01-02T00:00:00Z",
      as_of: "2026-01-07T00:00:00Z",
    })!;
    expect(range.start).toBe(Date.parse("2026-01-02T00:00:00Z"));
    expect(range.end).toBe(Date.parse("2026-01-07T00:00:00Z"));
    expect(range.points).toHaveLength(3);
  });

  it("distinguishes unrecorded history, a recorded zero, and a single active sample", () => {
    expect(capacityWindow(history([]))).toBeNull();
    expect(capacityWindow(history([point("04T00:00:00")]))?.hasCapacity).toBe(false);
    const sample = point("04T00:00:00", 1);
    const range = capacityWindow(history([sample]))!;
    expect(range.hasCapacity).toBe(true);
    expect(range.start).toBe(Date.parse(sample.at));
    expect(range.end).toBe(range.start);
    expect(range.points).toHaveLength(1);
  });

  it("keeps capacity changes within the same millisecond", () => {
    const range = capacityWindow(history([
      point("07T01:00:00.000100"),
      point("07T01:00:00.000500", 1000),
      point("07T01:00:00.000900"),
    ]))!;
    expect(range.start).toBeLessThan(range.end);
    expect(range.points.map(({ cpu_millis }) => cpu_millis)).toEqual([0, 1000, 0]);
    expect(capacityPointIndex(range.points, (range.start + range.end) / 2)).toBe(1);
  });
});

describe("capacity plot", () => {
  it("rounds corners locally and preserves flat levels between events", () => {
    const points = [
      { time: 0, cpu_millis: 50, memory_bytes: 0, cpu_occupied_millis: 0, memory_occupied_bytes: 0 },
      { time: 30, cpu_millis: 50, memory_bytes: 64, cpu_occupied_millis: 0, memory_occupied_bytes: 0 },
      { time: 100, cpu_millis: 0, memory_bytes: 64, cpu_occupied_millis: 0, memory_occupied_bytes: 0 },
      { time: 200, cpu_millis: 0, memory_bytes: 64, cpu_occupied_millis: 0, memory_occupied_bytes: 0 },
    ];
    const { samples } = capacitySeries(points, "cpu_millis", 10);
    expect(roundedStepPath(samples, (time) => time, (value) => 100 - value))
      .toBe("M0.00,50.00H92.00Q100.00,50.00 100.00,58.00V92.00Q100.00,100.00 108.00,100.00H200.00");
    expect(roundedStepPath(samples.slice(0, 1), (time) => time, (value) => value)).toBe("");
  });

  it("keeps brief extremes and time-weights dense intervals", () => {
    const points = [[0, 2], [1, 12], [2, 2], [3, 12], [4, 2], [6, 8], [7, 4], [9, 8], [10, 8]]
      .map(([time, value]) => ({
        time,
        cpu_millis: value,
        memory_bytes: 0,
        cpu_occupied_millis: 0,
        memory_occupied_bytes: 0,
      }));
    const { samples, bands } = capacitySeries(points, "cpu_millis", 2);
    expect(samples).toEqual([{ time: 0, value: 6 }, { time: 5, value: 5.2 }, { time: 10, value: 5.2 }]);
    expect(bands).toEqual([
      { left: 0, right: 5, low: 2, high: 12 },
      { left: 5, right: 10, low: 2, high: 8 },
    ]);
  });

  it("gives fractional and large pools readable scales from zero", () => {
    const small = capacityTicks(0.003);
    expect(small).toHaveLength(5);
    expect(small[4]).toBeCloseTo(0.0032, 10);
    expect(capacityTicks(60_000)).toEqual([0, 15_000, 30_000, 45_000, 60_000]);
  });
});
