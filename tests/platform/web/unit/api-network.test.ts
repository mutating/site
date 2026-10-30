import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, expect, it, vi } from "vitest";

let server: Server | undefined;
afterEach(async () => {
  if (server) {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server!.close(error => error ? reject(error) : resolve()));
    server = undefined;
  }
  vi.unstubAllGlobals();
});

/** Exercise the published package with real sockets: lost headers and truncated JSON/binary bodies must retry the same API URL and return complete data. */
it.each(["headers", "json", "blob"])("recovers from a real socket failure during %s", async failure => {
  const urls: string[] = [];
  const receivedStatuses: number[] = [];
  let breakBody: (() => void) | undefined;
  const bytes = new Uint8Array([0, 255, 128, 1]);
  server = createServer((request, response) => {
    urls.push(request.url!);
    if (urls.length === 1) {
      if (failure === "headers") response.destroy();
      else {
        response.writeHead(200, { "Content-Length": "1000" });
        response.flushHeaders();
        response.write(failure === "json" ? '{"ready":' : bytes.subarray(0, 2));
        breakBody = () => response.destroy();
      }
      return;
    }
    response.writeHead(200, { "X-Total-Count": "1", "Content-Type": failure === "blob" ? "image/png" : "application/json" });
    response.end(failure === "blob" ? bytes : '{"ready":true}');
  });
  await new Promise<void>(resolve => server!.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1`;
  vi.resetModules();
  vi.stubGlobal("window", { __TMC_API_URL__: base });
  vi.stubGlobal("navigator", {});
  const fetcher = globalThis.fetch;
  vi.stubGlobal("fetch", async (...args: Parameters<typeof fetcher>) => {
    const response = await fetcher(...args);
    receivedStatuses.push(response.status);
    // Break the real socket only after Fetch has delivered the headers, so a
    // body failure cannot accidentally pass as another pre-header failure.
    breakBody?.();
    breakBody = undefined;
    return response;
  });
  const { request, apiBlob } = await import("../../../../platform/web/src/lib/api");
  if (failure === "blob") {
    const blob = await apiBlob("users/me/avatar?v=one");
    expect(blob.type).toBe("image/png");
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(bytes);
  } else {
    await expect(request("users/me?view=full")).resolves.toEqual({ data: { ready: true }, total: 1 });
  }
  expect(urls).toEqual(failure === "blob"
    ? ["/v1/users/me/avatar?v=one", "/v1/users/me/avatar?v=one"]
    : ["/v1/users/me?view=full", "/v1/users/me?view=full"]);
  expect(receivedStatuses).toEqual(failure === "headers" ? [200] : [200, 200]);
});

/** A stalled HTTP error must keep its status, finish by its detail deadline and close the real connection rather than leaking an unread body. */
it("times out a real HTTP error body and closes its connection", async () => {
  let disconnected!: () => void;
  const closed = new Promise<void>(resolve => { disconnected = resolve; });
  let attempts = 0;
  server = createServer((_request, response) => {
    ++attempts;
    response.on("close", disconnected);
    response.writeHead(503, { "Content-Type": "application/json" });
    response.flushHeaders();
    response.write('{"detail":');
  });
  await new Promise<void>(resolve => server!.listen(0, "127.0.0.1", resolve));
  vi.resetModules();
  vi.stubGlobal("window", { __TMC_API_URL__: `http://127.0.0.1:${(server.address() as AddressInfo).port}/v1` });
  vi.stubGlobal("navigator", {});
  const { api } = await import("../../../../platform/web/src/lib/api");
  await expect(api("companies", { method: "POST", body: "{}" }, 1_000))
    .rejects.toMatchObject({ status: 503, detail: "" });
  await closed;
  expect(attempts).toBe(1);
});
