import { afterEach, beforeEach, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules(); vi.useFakeTimers();
  vi.stubGlobal("window", { __TMC_API_URL__: "https://api.example/v1" });
  vi.stubGlobal("navigator", {});
  vi.spyOn(AbortSignal, "timeout").mockImplementation(milliseconds => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("Timed out", "TimeoutError")), milliseconds);
    return controller.signal;
  });
  vi.stubGlobal("fetch", vi.fn((_url, options) => new Promise<Response>((resolve, reject) => {
    setTimeout(() => resolve(new Response(JSON.stringify({ ready: true }))), 22_000);
    options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
  })));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

/** A valid aggregate read must survive 20 seconds while ordinary requests keep their existing deadline. */
it("permits a per-request discovery budget without changing the default deadline", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  const results = Promise.allSettled([api("github/repositories", {}, 90_000), api("users/me")]);
  await vi.advanceTimersByTimeAsync(22_000);
  const [discovery, ordinary] = await results;
  expect(discovery).toEqual({ status: "fulfilled", value: { ready: true } });
  expect(ordinary).toMatchObject({ status: "rejected", reason: { status: 0 } });
});

/** Leaving a page must still cancel its long discovery immediately instead of waiting for the larger budget. */
it("preserves caller cancellation with a discovery deadline", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  const controller = new AbortController();
  const pending = api("github/repositories", { signal: controller.signal }, 90_000);
  const cancelled = expect(pending).rejects.toMatchObject({ name: "AbortError" });
  controller.abort();
  await cancelled;
});
