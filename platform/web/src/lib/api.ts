import { createClient, HTTPError } from "reboop";
import { tabNotifications } from "./tabNotifications";

export const API_BASE = window.__TMC_API_URL__;
export const apiURL = (path: string) => `${API_BASE}/${path}`;

let sessionRevision = 0;
let expectedUserId: string | null = null;
let sessionQueue: Promise<unknown> = Promise.resolve();

export function advanceSessionRevision(userId = expectedUserId) {
  expectedUserId = userId;
  ++sessionRevision;
}

const sessionChanged = (message: unknown) => {
  const owner = message && typeof message === "object" && "userId" in message ? message.userId : null;
  window.dispatchEvent(new CustomEvent("session-changed", { detail: typeof owner === "string" ? owner : null }));
};
const notifySessionChange = tabNotifications(`tmc-session-changed:${API_BASE}`, sessionChanged);

function withSessionLock<T>(run: () => Promise<T>): Promise<T> {
  // Cookie writes are shared by tabs. Finish an old logout before a new login
  // can issue its cookie, including when the requests return out of order.
  if (navigator.locks) return navigator.locks.request(`tmc-session:${API_BASE}`, run);
  const pending = sessionQueue.then(run, run);
  sessionQueue = pending.catch(() => {});
  return pending;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    public organizationMembershipId?: string,
    public organizationId?: string,
    public poolCheckRevision?: number,
  ) {
    super(detail);
  }
}
export const DEFAULT_TIMEOUT_MS = 20_000;
const client = createClient({
  baseUrl: globalThis.location?.href,
  headers: {
    "Content-Type": "application/json",
    "X-Requested-With": "ThrongDashboard",
  },
});
type ReadBody<T> = (response: Response) => Promise<T>;
export type ApiResponse<T> = {
  data: T;
  total: number | null;
  unfilteredTotal?: number;
  organizationMembershipId?: string;
  organizationId?: string;
  poolCheckRevision?: number;
  computerReadAllowed?: boolean;
  computerPoolAssigned?: boolean;
};

function poolCheckRevision(headers: Headers) {
  const revision = headers.get("X-Computer-Pool-Check-Revision");
  return revision !== null && Number.isSafeInteger(Number(revision)) ? Number(revision) : undefined;
}

export function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<ApiResponse<T>> {
  return requestWithReader<T>(path, options, timeoutMs, response => response.json());
}

export async function apiBlob(path: string, options: RequestInit = {}, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<Blob> {
  return (await requestWithReader(path, options, timeoutMs, response => response.blob())).data;
}

async function requestWithReader<T>(
  path: string,
  options: RequestInit,
  timeoutMs: number,
  readBody: ReadBody<T>,
): Promise<ApiResponse<T>> {
  const method = (options.method ?? "GET").toUpperCase();
  const changesCookie = path === "auth/session" && (method === "POST" || method === "DELETE")
    || method === "DELETE" && /^users\/[\da-f-]{36}$/i.test(path);
  if (changesCookie) return withSessionLock(async () => {
    const result = await sendRequest(path, options, ++sessionRevision, timeoutMs, readBody);
    const profile = result.data;
    const userId = method === "POST" && profile && typeof profile === "object" &&
      "id" in profile && typeof profile.id === "string" ? profile.id : null;
    notifySessionChange({ userId });
    return result;
  });
  return sendRequest(path, options, sessionRevision, timeoutMs, readBody);
}

// reboop protects successful body reads. HTTPError exposes a raw Response, so
// bound its optional detail separately and cancel its connection on timeout.
async function errorDetail(response: Response, controller: AbortController, signal: AbortSignal, timeoutMs: number): Promise<string> {
  let cancel = () => {};
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const body = await Promise.race([
      response.json().catch(() => null),
      new Promise<null>(resolve => {
        cancel = () => resolve(null);
        signal.addEventListener("abort", cancel, { once: true });
        if (signal.aborted) cancel();
      }),
    ]);
    return typeof body?.detail === "string" ? body.detail : "";
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}

async function sendRequest<T>(
  path: string,
  options: RequestInit,
  revision: number,
  timeoutMs: number,
  readBody: ReadBody<T>,
): Promise<ApiResponse<T>> {
  options.signal?.throwIfAborted();
  const controller = new AbortController();
  const signal = AbortSignal.any([controller.signal, ...(options.signal ? [options.signal] : [])]);
  const headers = new Headers(options.headers);
  if (expectedUserId && path !== "auth/session") headers.set("X-Expected-User-Id", expectedUserId);
  try {
    return await client.read(apiURL(path), async response => ({
      data: (response.status === 204 || options.method?.toUpperCase() === "HEAD"
        ? undefined : await readBody(response)) as T,
      total: response.headers.has("X-Total-Count") ? Number(response.headers.get("X-Total-Count")) : null,
      ...(response.headers.has("X-Total-Unfiltered-Count")
        ? { unfilteredTotal: Number(response.headers.get("X-Total-Unfiltered-Count")) } : {}),
      ...(response.headers.has("X-Organization-Membership-Id")
        ? { organizationMembershipId: response.headers.get("X-Organization-Membership-Id")! } : {}),
      ...(response.headers.has("X-Organization-Id")
        ? { organizationId: response.headers.get("X-Organization-Id")! } : {}),
      ...(response.headers.has("X-Computer-Pool-Check-Revision")
        ? { poolCheckRevision: poolCheckRevision(response.headers) } : {}),
      ...(response.headers.has("X-Computer-Read-Allowed")
        ? { computerReadAllowed: response.headers.get("X-Computer-Read-Allowed") === "true" } : {}),
      ...(response.headers.has("X-Computer-Pool-Assigned")
        ? { computerPoolAssigned: response.headers.get("X-Computer-Pool-Assigned") === "true" } : {}),
    }), { ...options, headers, signal, credentials: "include", timeoutMs });
  } catch (error) {
    options.signal?.throwIfAborted();
    if (error instanceof HTTPError) {
      if (error.response.status === 401 && path !== "auth/session" && revision === sessionRevision)
        window.dispatchEvent(new Event("session-expired"));
      const detail = await errorDetail(error.response, controller, signal, timeoutMs);
      options.signal?.throwIfAborted();
      if (error.response.status === 409 && detail === "Account session changed" && revision === sessionRevision) {
        sessionChanged(null);
      }
      throw new ApiError(
        error.response.status,
        detail,
        error.response.headers.get("X-Organization-Membership-Id") ?? undefined,
        error.response.headers.get("X-Organization-Id") ?? undefined,
        poolCheckRevision(error.response.headers),
      );
    }
    if (error instanceof SyntaxError || error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError(0, "Network error");
  }
}

export const api = async <T>(path: string, options?: RequestInit, timeoutMs?: number) =>
  (await request<T>(path, options, timeoutMs)).data;
export const json = (method: string, data?: unknown): RequestInit => ({
  method,
  body: data === undefined ? undefined : JSON.stringify(data),
});
export async function withDeadline<T>(
  signal: AbortSignal | undefined,
  load: (signal: AbortSignal) => Promise<T>,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(), timeoutMs);
  try {
    return await load(signal ? AbortSignal.any([signal, deadline.signal]) : deadline.signal);
  } finally {
    clearTimeout(timer);
  }
}

export async function allPages<T>(
  path: string,
  signal?: AbortSignal,
  { pageSize = 200, deadlineMs, onPage }: {
    pageSize?: number;
    deadlineMs?: number;
    onPage?: (result: ApiResponse<T[]>) => void;
  } = {},
): Promise<T[]> {
  const rows: T[] = [];
  for (let offset = 0; ; offset += pageSize) {
    const pagePath = `${path}${path.includes("?") ? "&" : "?"}limit=${pageSize}&offset=${offset}`;
    const read = (pageSignal?: AbortSignal) => request<T[]>(pagePath, { signal: pageSignal });
    const result = deadlineMs === undefined
      ? await read(signal)
      : await withDeadline(signal, read, deadlineMs);
    if (!signal?.aborted) onPage?.(result);
    const page = result.data;
    rows.push(...page);
    if (page.length < pageSize) return rows;
  }
}
