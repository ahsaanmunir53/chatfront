// Console panels read the API every few seconds while the server is up. A panel the person's
// role does not allow passes no loader and asks nothing.
import { useCallback, useEffect, useState } from "react";
import { useServer } from "./server";

/** The latest result of `load` (memoised by the caller), read now and every `everyMs`. */
export function usePolled<T>(load: (() => Promise<T>) | null, everyMs: number) {
  const { state } = useServer();
  const [data, setData] = useState<T | null>(null);
  const refresh = useCallback(() => {
    load?.().then(setData, () => {
      // The last result stays on screen; the next poll tries again.
    });
  }, [load]);
  useEffect(() => {
    if (state !== "online" || !load) return;
    refresh();
    const timer = window.setInterval(refresh, everyMs);
    return () => window.clearInterval(timer);
  }, [state, load, refresh, everyMs]);
  return { data, refresh };
}
