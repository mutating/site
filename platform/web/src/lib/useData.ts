import { useEffect, useRef, useState } from "react";
export function useData<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string,
  retainData = false,
) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const previousKey = useRef<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    if (!retainData || previousKey.current !== key) setData(null);
    previousKey.current = key;
    load(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [key, revision, retainData]);
  return {
    data,
    error,
    loading,
    update: setData,
    reload: () => setRevision((x) => x + 1),
  };
}
