import { useCallback, useEffect, useRef, useState } from "react";

export function useRefreshWhileVisible(reload: () => void, enabled = true, refreshMs = 60_000) {
  useEffect(() => {
    if (!enabled) return;
    const refresh = () => { if (document.visibilityState === "visible") reload(); };
    const timer = window.setInterval(refresh, refreshMs);
    window.addEventListener("focus", refresh);
    window.addEventListener("online", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("online", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [enabled, refreshMs, reload]);
}

export function useData<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string,
  retainData = false,
  { refreshKey, retainError = false, acceptError }: {
    refreshKey?: number;
    retainError?: boolean;
    acceptError?: (error: unknown) => boolean;
  } = {},
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const active = useRef<AbortController | null>(null);
  const reload = useCallback(() => {
    active.current?.abort();
    setLoading(true);
    setRevision((x) => x + 1);
  }, []);
  const previousKey = useRef<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    active.current = controller;
    setLoading(true);
    if (!retainError || previousKey.current !== key) setError(null);
    if (!retainData || previousKey.current !== key) setData(null);
    previousKey.current = key;
    load(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setData(value);
          setError(null);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted && acceptError?.(e) !== false) setError(e);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [key, revision, retainData, refreshKey, retainError]);
  return {
    data,
    error,
    loading,
    update: setData,
    clearError: () => setError(null),
    reload,
  };
}
