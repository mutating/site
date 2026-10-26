import { useEffect, useState, type ReactNode } from "react";
import { apiBlob } from "../lib/api";

/** Download API images through the same authenticated, cancellable retry transport as JSON. */
export function ApiImage({ path, fallback }: { path: string | null; fallback: ReactNode }) {
  const [image, setImage] = useState<{ path: string; src: string } | null>(null);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  useEffect(() => {
    setImage(null);
    if (!path) return;
    const controller = new AbortController();
    let src: string | undefined;
    apiBlob(path, { signal: controller.signal }).then(blob => {
      if (controller.signal.aborted) return;
      src = URL.createObjectURL(blob);
      setImage({ path, src });
    }).catch(() => {
      // HTTP failures keep the placeholder; transport failures retry in apiBlob.
    });
    return () => {
      controller.abort();
      if (src) URL.revokeObjectURL(src);
    };
  }, [path]);
  const src = image?.path === path ? image.src : null;
  return src && failedSrc !== src
    ? <img key={src} src={src} alt="" onError={() => setFailedSrc(src)} />
    : <>{fallback}</>;
}
