import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const response = (status: number, data = {}) => new Response(JSON.stringify(data), {
  status, headers: { "Content-Type": "application/json" },
});

describe("browser session requests", () => {
  const fetchMock = vi.fn();
  const dispatchEvent = vi.fn();
  beforeEach(() => {
    vi.resetModules();
    fetchMock.mockReset();
    dispatchEvent.mockReset();
    vi.stubGlobal("window", { __TMC_API_URL__: "https://api.example/v1", dispatchEvent });
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("queues cookie writes without Web Locks and releases the queue after failure", async () => {
    const { api, json } = await import("../../../../platform/web/src/lib/api");
    let release!: (value: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve; }));
    fetchMock.mockResolvedValueOnce(response(200, { id: "new-user" }));
    const oldLogout = api("auth/session", json("DELETE"));
    const failed = expect(oldLogout).rejects.toMatchObject({ status: 503 });
    const newLogin = api("auth/session", json("POST", { login: "new-user" }));
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    release(response(503));
    await failed;
    await expect(newLogin).resolves.toEqual({ id: "new-user" });
    expect(fetchMock.mock.calls.map(([, options]) => options.method)).toEqual(["DELETE", "POST"]);
  });

  it("ignores a previous login's 401 while current unauthorized requests still expire the session", async () => {
    const { api, json } = await import("../../../../platform/web/src/lib/api");
    let release!: (value: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve; }));
    fetchMock.mockResolvedValueOnce(response(200, { id: "new-user" }));
    const oldRequest = api("users/old-user", json("PATCH", { first_name: "Old" }));
    const rejected = expect(oldRequest).rejects.toMatchObject({ status: 401 });
    await api("auth/session", json("POST", { login: "new-user" }));
    release(response(401));
    await rejected;
    expect(dispatchEvent).not.toHaveBeenCalled();
    fetchMock.mockResolvedValueOnce(response(401));
    await expect(api("users/me")).rejects.toMatchObject({ status: 401 });
    expect(dispatchEvent).toHaveBeenCalledTimes(1);
    expect(dispatchEvent.mock.calls[0][0].type).toBe("session-expired");
  });
});
