import { afterEach, beforeEach, expect, it, vi } from "vitest";

const fetchMock = vi.fn();
const dispatchEvent = vi.fn();

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  fetchMock.mockReset();
  dispatchEvent.mockReset();
  vi.stubGlobal("window", { __TMC_API_URL__: "https://api.example/v1", dispatchEvent });
  vi.stubGlobal("navigator", {});
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** Keep the API prefix, query, JSON payload, CSRF marker and cookies together; losing any breaks authenticated writes. */
it("sends authenticated JSON writes to the configured API prefix", async () => {
  const { api, json } = await import("../../../../platform/web/src/lib/api");
  let sent!: Request;
  fetchMock.mockImplementation(async (input, init) => {
    sent = new Request(input, init);
    return new Response('{"saved":true}', { status: 201 });
  });
  await expect(api("companies?source=platform", json("POST", { name: "Команда" })))
    .resolves.toEqual({ saved: true });
  expect(sent.url).toBe("https://api.example/v1/companies?source=platform");
  expect(sent.method).toBe("POST");
  expect(sent.credentials).toBe("include");
  expect(sent.headers.get("Content-Type")).toBe("application/json");
  expect(sent.headers.get("X-Requested-With")).toBe("ThrongDashboard");
  expect(await sent.json()).toEqual({ name: "Команда" });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

/** Both Fetch header representations must merge case-insensitively with the CSRF default, including content-type overrides. */
it.each([
  new Headers({ "content-type": "image/png", "X-Trace": "avatar" }),
  [["content-type", "image/png"], ["X-Trace", "avatar"]] as [string, string][],
])("merges caller headers without losing authentication defaults: %s", async headers => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  let sent!: Request;
  fetchMock.mockImplementation((input, init) => {
    sent = new Request(input, init);
    return new Response(null, { status: 204 });
  });
  const bytes = new Uint8Array([0, 255, 128]);
  await api("users/me/avatar", { method: "PUT", headers, body: bytes });
  expect(sent.headers.get("Content-Type")).toBe("image/png");
  expect(sent.headers.get("X-Trace")).toBe("avatar");
  expect(sent.headers.get("X-Requested-With")).toBe("ThrongDashboard");
  expect(sent.credentials).toBe("include");
  expect(new Uint8Array(await sent.arrayBuffer())).toEqual(bytes);
});

/** Pagination must preserve filters, offsets and abort signals while exposing total counts and bodyless 204 responses. */
it("preserves collection metadata, pagination and empty responses", async () => {
  const { request, allPages } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockResolvedValueOnce(new Response('[{"id":1}]', { headers: { "X-Total-Count": "3" } }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(new Response('[1,2]'))
    .mockResolvedValueOnce(new Response('[3]'));
  await expect(request("companies")).resolves.toEqual({ data: [{ id: 1 }], total: 3 });
  await expect(request("companies/one", { method: "DELETE" })).resolves.toEqual({ data: undefined, total: null });
  const controller = new AbortController();
  await expect(allPages("computers?user_id=one", controller.signal, 2)).resolves.toEqual([1, 2, 3]);
  expect(fetchMock.mock.calls.slice(2).map(([input]) => new Request(input).url)).toEqual([
    "https://api.example/v1/computers?user_id=one&limit=2&offset=0",
    "https://api.example/v1/computers?user_id=one&limit=2&offset=2",
  ]);
  controller.abort();
  expect(fetchMock.mock.calls[2][1].signal.aborted).toBe(true);
  expect(fetchMock.mock.calls[3][1].signal.aborted).toBe(true);
});

/** Error payloads are application data: retain string detail and HTTP status, tolerating HTML or structured validation errors. */
it.each([
  ['{"detail":"Permission denied"}', "Permission denied"],
  ['{"detail":[{"msg":"Invalid"}]}', ""],
  ["<html>Unavailable</html>", ""],
])("preserves an HTTP error without retrying its body: %s", async (body, detail) => {
  const { api, ApiError } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockResolvedValue(new Response(body, { status: 403 }));
  const error = await api("companies").catch(error => error);
  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({ status: 403, detail, message: detail });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

/** An error body can stall after headers; it must release the UI with the known status instead of hanging or replaying a write. */
it.each([true, false])("bounds a stalled HTTP error body and preserves the HTTP status (body observes cancellation: %s)", async cooperative => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockImplementation((_input, init) => {
    const response = new Response(null, { status: 503 });
    vi.spyOn(response, "json").mockImplementation(() => new Promise((_resolve, reject) => {
      if (cooperative) init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
    }));
    return response;
  });
  const pending = expect(api("companies", { method: "POST", body: "{}" }, 50))
    .rejects.toMatchObject({ status: 503, detail: "" });
  await vi.advanceTimersByTimeAsync(50);
  await pending;
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
});

/** Cancellation while an error body is streaming must preserve the caller's exact reason and remove its deadline. */
it("cancels error-body decoding with the caller's reason", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  let reading!: () => void;
  const started = new Promise<void>(resolve => { reading = resolve; });
  fetchMock.mockImplementation((_input, init) => {
    const response = new Response(null, { status: 403 });
    vi.spyOn(response, "json").mockImplementation(() => {
      reading();
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
      });
    });
    return response;
  });
  const controller = new AbortController();
  const reason = new Error("Navigation changed");
  const pending = expect(api("companies", { signal: controller.signal })).rejects.toBe(reason);
  await started;
  controller.abort(reason);
  await pending;
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});

/** Authentication probes must not expire a session; only a current protected request may dispatch the event. */
it("keeps expected login failures separate from session expiration", async () => {
  const { api, advanceSessionRevision } = await import("../../../../platform/web/src/lib/api");
  let release!: (response: Response) => void;
  fetchMock.mockResolvedValueOnce(new Response('{"detail":"Invalid login"}', { status: 401 }))
    .mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve; }));
  await expect(api("auth/session", { method: "POST" })).rejects.toMatchObject({ status: 401, detail: "Invalid login" });
  expect(dispatchEvent).not.toHaveBeenCalled();
  const pending = expect(api("users/me")).rejects.toMatchObject({ status: 401 });
  advanceSessionRevision();
  release(new Response("{}", { status: 401 }));
  await pending;
  expect(dispatchEvent).not.toHaveBeenCalled();
});

/** The Web Locks branch must serialize login/logout/account deletion under the same API-specific lock across tabs. */
it.each([
  ["auth/session", "POST"],
  ["auth/session", "DELETE"],
  ["users/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "DELETE"],
])("uses the cross-tab cookie lock for %s %s", async (path, method) => {
  const locks = { request: vi.fn((_name, run) => run()) };
  vi.stubGlobal("navigator", { locks });
  const { api } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
  await api(path, { method });
  expect(locks.request).toHaveBeenCalledTimes(1);
  expect(locks.request.mock.calls[0][0]).toBe("tmc-session:https://api.example/v1");
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

/** Aborting a page must settle even if a fetch implementation ignores its signal; no second request or live timer may remain. */
it("cancels an in-flight non-cooperative transport without waiting for its deadline", async () => {
  const { api } = await import("../../../../platform/web/src/lib/api");
  fetchMock.mockImplementation(() => new Promise(() => {}));
  const controller = new AbortController();
  const reason = new Error("Page was replaced");
  const pending = expect(api("users/me", { signal: controller.signal })).rejects.toBe(reason);
  controller.abort(reason);
  await pending;
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(vi.getTimerCount()).toBe(0);
});
