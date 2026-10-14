import { describe, expect, it } from "vitest";
import { stepPath } from "../../../../platform/web/src/lib/chart";
const sample = (at: string, cpu: number) => ({
  at,
  cpu_millis: cpu,
  memory_bytes: 0,
  cpu_occupied_millis: 0,
  memory_occupied_bytes: 0,
});
describe("capacity chart", () => {
  it("keeps discontinuous capacities stepwise instead of inventing interpolated capacity", () => {
    const points = [
      sample("2026-10-01T00:00:00Z", 2000),
      sample("2026-10-01T01:00:00Z", 0),
      sample("2026-10-01T02:00:00Z", 4000),
    ];
    expect(
      stepPath(
        points,
        "cpu_millis",
        Date.parse(points[0].at),
        Date.parse(points[2].at),
        4000,
        200,
        100,
      ),
    ).toBe("M0.00,50.00H100.00V100.00H200.00V0.00");
  });
  it("has no fabricated line before data collection or for an empty interval", () => {
    expect(stepPath([], "cpu_millis", 0, 100, 1000)).toBe("");
    expect(
      stepPath(
        [sample("1970-01-01T00:00:00.050Z", 500)],
        "cpu_millis",
        0,
        100,
        1000,
        100,
        100,
      ),
    ).toBe("M50.00,50.00");
    expect(
      stepPath([sample("1970-01-01T00:00:00Z", 0)], "cpu_millis", 0, 0, 0),
    ).toBe("");
  });
});
