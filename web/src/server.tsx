import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ping } from "./api";
import { QUIET_MS, stateAfterMiss } from "./wake";
import type { ServerState } from "./wake";

const RETRY_MS = 3000;

type Server = { state: ServerState; startedAt: number; retry: () => void };

const ServerContext = createContext<Server | null>(null);

/** Pings /health until the API answers, so pages can open at once and wait politely. */
export function ServerProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ServerState>("checking");
  const [startedAt, setStartedAt] = useState(() => Date.now());
  const [attempt, setAttempt] = useState(0);
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    const begun = Date.now();
    let timer: number | undefined;
    const quiet = window.setTimeout(() => {
      if (live.current) setState((s) => (s === "checking" ? "waking" : s));
    }, QUIET_MS);

    const check = async () => {
      const ok = await ping(RETRY_MS * 4);
      if (!live.current) return;
      if (ok) {
        window.clearTimeout(quiet);
        setState("online");
        return;
      }
      const next = stateAfterMiss(begun, Date.now());
      setState(next);
      if (next === "waking") timer = window.setTimeout(check, RETRY_MS);
    };
    void check();
    return () => {
      live.current = false;
      window.clearTimeout(quiet);
      window.clearTimeout(timer);
    };
  }, [attempt]);

  const retry = useCallback(() => {
    setStartedAt(Date.now());
    setState("checking");
    setAttempt((n) => n + 1);
  }, []);

  return (
    <ServerContext.Provider value={{ state, startedAt, retry }}>{children}</ServerContext.Provider>
  );
}

export function useServer(): Server {
  const server = useContext(ServerContext);
  if (!server) throw new Error("useServer must be used inside ServerProvider");
  return server;
}
