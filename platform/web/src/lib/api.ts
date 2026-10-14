export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}
export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<{ data: T; total: number | null }> {
  let response: Response;
  try {
    response = await fetch(`/account/api/${path}`, {
      ...options,
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "ThrongDashboard",
        ...options.headers,
      },
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError(0, "Network error");
  }
  if (!response.ok) {
    if (response.status === 401 && !["session", "register"].includes(path))
      window.dispatchEvent(new Event("session-expired"));
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : "";
    } catch {}
    throw new ApiError(response.status, detail);
  }
  const data = response.status === 204 ? undefined : await response.json();
  return {
    data: data as T,
    total: response.headers.has("X-Total-Count")
      ? Number(response.headers.get("X-Total-Count"))
      : null,
  };
}
export const api = async <T>(path: string, options?: RequestInit) =>
  (await request<T>(path, options)).data;
export const json = (method: string, data?: unknown): RequestInit => ({
  method,
  body: data === undefined ? undefined : JSON.stringify(data),
});
export async function allPages<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T[]> {
  const rows: T[] = [];
  for (let offset = 0; ; offset += 200) {
    const page = await api<T[]>(
      `${path}${path.includes("?") ? "&" : "?"}limit=200&offset=${offset}`,
      { signal },
    );
    rows.push(...page);
    if (page.length < 200) return rows;
  }
}
