import { afterEach, beforeEach, expect, it, vi } from "vitest";

const fetchMock = vi.fn();
const response = (status = 200) => new Response(JSON.stringify({ ready: true }), { status });

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  fetchMock.mockReset();
  vi.stubGlobal("window", { __TMC_API_URL__: "https://api.example/v1", dispatchEvent: vi.fn() });
  vi.stubGlobal("navigator", {});
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(Math, "random").mockReturnValue(0);
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("recovers after repeated transport failures using the same hostname and capped backoff", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  let attempts = 0;
  fetchMock.mockImplementation(async () => {
    if (++attempts <= 6) throw new TypeError("Failed to fetch");
    return response();
  });
  const pending = api("users/me");
  await vi.advanceTimersByTimeAsync(0);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  for (const delay of [1_000, 2_000, 4_000, 8_000, 10_000, 10_000]) {
    await vi.advanceTimersByTimeAsync(delay - 1);
    const before = fetchMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(before + 1);
  }
  await expect(pending).resolves.toEqual({ ready: true });
  for (const [url, options] of fetchMock.mock.calls) {
    expect(url).toBe("https://api.example/v1/users/me");
    expect(options.credentials).toBe("include");
    expect(options.headers["X-Requested-With"]).toBe("ThrongDashboard");
  }
  expect(vi.getTimerCount()).toBe(0);
});

it("aborts a stalled attempt at its deadline and gives the next attempt a fresh signal", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockImplementationOnce((_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
  })).mockResolvedValueOnce(response());
  const pending = api("users/me");
  await vi.advanceTimersByTimeAsync(20_000);
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(1_000);
  await expect(pending).resolves.toEqual({ ready: true });
  expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(false);
  expect(vi.getTimerCount()).toBe(0);
});

it("retries a transport failure while reading the response body", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  const broken = response();
  vi.spyOn(broken, "json").mockRejectedValue(new TypeError("Connection closed"));
  fetchMock.mockResolvedValueOnce(broken).mockResolvedValueOnce(response());
  const pending = api("users/me");
  await vi.advanceTimersByTimeAsync(1_000);
  await expect(pending).resolves.toEqual({ ready: true });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it.each(["fetch", "body"])("retries binary API reads after a %s failure without decoding JSON", async failure => {
  const { apiBlob } = await import("../../../../platform/web/src/lib/api");
  const bytes = new Uint8Array([0, 255, 128, 1]);
  if (failure === "fetch") fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
  else {
    const broken = new Response(bytes);
    vi.spyOn(broken, "blob").mockRejectedValue(new TypeError("Connection closed"));
    fetchMock.mockResolvedValueOnce(broken);
  }
  fetchMock.mockResolvedValueOnce(new Response(bytes, { headers: { "Content-Type": "image/webp" } }));
  const pending = apiBlob("users/me/avatar?v=hash");
  await vi.advanceTimersByTimeAsync(1_000);
  const blob = await pending;
  expect(blob.type).toBe("image/webp");
  expect(new Uint8Array(await blob.arrayBuffer())).toEqual(bytes);
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls[1][0]).toBe("https://api.example/v1/users/me/avatar?v=hash");
  expect(fetchMock.mock.calls[1][1].credentials).toBe("include");
});

it("retries HEAD without attempting to parse its empty body", async () => {
  const { request } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(new Response(null, { headers: { "X-Total-Count": "7" } }));
  const pending = request("companies", { method: "head" });
  await vi.advanceTimersByTimeAsync(1_000);
  await expect(pending).resolves.toEqual({ data: undefined, total: 7 });
});

it("retries a deadline that fires during body streaming even when the body reports AbortError", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockImplementationOnce((_url, options) => {
    const stalled = response();
    vi.spyOn(stalled, "json").mockImplementation(() => new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    return stalled;
  }).mockResolvedValueOnce(response());
  const pending = api("users/me");
  await vi.advanceTimersByTimeAsync(21_000);
  await expect(pending).resolves.toEqual({ ready: true });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it.each([401, 403, 404, 429, 500, 503])("does not retry HTTP %s", async status => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockResolvedValue(response(status));
  await expect(api("users/me")).rejects.toMatchObject({ status });
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it.each(["POST", "PUT", "PATCH", "DELETE"])("does not replay %s after a lost response", async method => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockRejectedValue(new TypeError("Connection closed after commit"));
  await expect(api("companies", { method, body: "{}" })).rejects.toMatchObject({ status: 0 });
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("does not retry invalid JSON", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockResolvedValue(new Response("broken JSON"));
  await expect(api("users/me")).rejects.toBeInstanceOf(SyntaxError);
  expect(vi.getTimerCount()).toBe(0);
});

it("cancels backoff immediately with the caller's reason and leaves no timer", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
  const controller = new AbortController();
  const reason = new Error("Page was replaced");
  const pending = api("users/me", { signal: controller.signal });
  const cancelled = expect(pending).rejects.toBe(reason);
  await vi.advanceTimersByTimeAsync(0);
  controller.abort(reason);
  await cancelled;
  await vi.advanceTimersByTimeAsync(60_000);
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

it("does not start an already cancelled read", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  const controller = new AbortController();
  controller.abort();
  await expect(api("users/me", { signal: controller.signal })).rejects.toMatchObject({ name: "AbortError" });
  expect(fetchMock).not.toHaveBeenCalled();
  expect(vi.getTimerCount()).toBe(0);
});
