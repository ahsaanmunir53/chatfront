import { useEffect, useState } from "react";
import { ApiError } from "./api";

export type Loaded<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "missing" }
  | { state: "failed" };

/** Load data for a page; a 404 is "missing", anything else is "failed". */
export function useLoad<T>(load: () => Promise<T>, key: string): Loaded<T> {
  const [result, setResult] = useState<Loaded<T>>({ state: "loading" });
  useEffect(() => {
    let live = true;
    setResult({ state: "loading" });
    load().then(
      (data) => live && setResult({ state: "ready", data }),
      (error: unknown) => {
        if (!live) return;
        const missing = error instanceof ApiError && error.status === 404;
        setResult({ state: missing ? "missing" : "failed" });
      },
    );
    return () => {
      live = false;
    };
    // `load` is a new function on every render; `key` says when the data really changes.
  }, [key]);
  return result;
}
