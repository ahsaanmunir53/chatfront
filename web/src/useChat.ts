// One chat with one shop: polling, sending in order (also what was typed while the server
// slept), photos and voice notes. A customer gets a random chat token kept in this browser;
// the owner console passes its signed session instead.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "./api";
import type { ChatAuth, ServerMessage } from "./api";
import { awaitingReply, lastSeen, merge, settle } from "./chat";
import type { Outgoing } from "./chat";
import { useServer } from "./server";

const POLL_MS = 1500;
const RETRY_MS = 3000;

export type SendProblem = "network" | "type" | "size" | "signed_out";

function storedToken(slug: string): string | null {
  try {
    return localStorage.getItem(`chatfront.chat.${slug}`);
  } catch {
    return null;
  }
}

function storeToken(slug: string, token: string): void {
  try {
    localStorage.setItem(`chatfront.chat.${slug}`, token);
  } catch {
    // Without storage the chat still works; it starts fresh on the next visit.
  }
}

function problemOf(error: unknown): { problem: SendProblem; drop: boolean } {
  if (!(error instanceof ApiError)) return { problem: "network", drop: false };
  if (error.status === 401) return { problem: "signed_out", drop: true };
  if (error.status === 413) return { problem: "size", drop: true };
  if (error.status === 415) return { problem: "type", drop: true };
  return { problem: "network", drop: error.status < 500 && error.status !== 429 };
}

export function useChat(slug: string, ownerToken: string | null, onSignedOut?: () => void) {
  const { state } = useServer();
  const online = state === "online";
  const [customerToken, setCustomerToken] = useState(() =>
    ownerToken ? null : storedToken(slug),
  );
  const auth: ChatAuth | null = ownerToken
    ? { token: ownerToken, owner: true }
    : customerToken
      ? { token: customerToken, owner: false }
      : null;
  const authToken = auth?.token;
  const isOwner = auth?.owner ?? false;
  const [messages, setMessages] = useState<ServerMessage[]>([]);
  const [pending, setPending] = useState<Outgoing[]>([]);
  const [sent, setSent] = useState<Set<string>>(() => new Set());
  const [problem, setProblem] = useState<SendProblem | null>(null);
  const [retry, setRetry] = useState(0);
  const inFlight = useRef(false);
  const signedOut = useRef(onSignedOut);
  signedOut.current = onSignedOut;

  const ensureAuth = useCallback(async (): Promise<ChatAuth> => {
    if (authToken) return { token: authToken, owner: isOwner };
    const { token } = await api.startChat(slug);
    storeToken(slug, token);
    setCustomerToken(token);
    return { token, owner: false };
  }, [slug, authToken, isOwner]);

  // Poll for new messages once the server is awake.
  useEffect(() => {
    if (!authToken || !online) return;
    const current: ChatAuth = { token: authToken, owner: isOwner };
    let live = true;
    let after: string | undefined;
    const poll = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const fresh = await api.listChat(slug, current, after);
        if (!live || fresh.length === 0) return;
        after = lastSeen(fresh);
        setMessages((known) => merge(known, fresh));
      } catch (error) {
        if (live && error instanceof ApiError && error.status === 401) signedOut.current?.();
        // Anything else: a missed poll is retried on the next tick.
      }
    };
    void poll();
    const timer = window.setInterval(poll, POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [slug, authToken, isOwner, online]);

  // What the server has shown back is no longer pending; its photo preview can go.
  useEffect(() => {
    setPending((waiting) => {
      const still = settle(waiting, messages);
      for (const done of waiting) {
        if (done.preview && !still.includes(done)) URL.revokeObjectURL(done.preview);
      }
      return still.length === waiting.length ? waiting : still;
    });
  }, [messages]);

  // Send one at a time, in order, once the server is awake. A failed send is retried unless
  // the server refused it for good (a file it cannot read, a session that ended).
  useEffect(() => {
    if (!online || inFlight.current) return;
    const next = pending.find((p) => !sent.has(p.clientId));
    if (!next) return;
    inFlight.current = true;
    (async () => {
      try {
        const current = await ensureAuth();
        if (next.file) {
          await api.postMedia(slug, current, next.clientId, next.file, next.body || undefined);
        } else {
          await api.postChat(slug, current, {
            client_id: next.clientId,
            text: next.body,
            ...(next.buttonId ? { button_id: next.buttonId } : {}),
          });
        }
        setSent((done) => new Set(done).add(next.clientId));
      } catch (error) {
        const { problem: kind, drop } = problemOf(error);
        setProblem(kind);
        if (kind === "signed_out") signedOut.current?.();
        if (drop) setPending((waiting) => waiting.filter((p) => p.clientId !== next.clientId));
        else window.setTimeout(() => setRetry((n) => n + 1), RETRY_MS);
      } finally {
        inFlight.current = false;
      }
    })();
  }, [online, pending, sent, slug, ensureAuth, retry]);

  const queue = useCallback((item: Omit<Outgoing, "clientId">) => {
    setProblem(null);
    setPending((waiting) => [...waiting, { ...item, clientId: crypto.randomUUID() }]);
  }, []);

  const say = useCallback(
    (text: string, buttonId?: string) => {
      const body = text.trim();
      if (body) queue({ kind: "text", body, ...(buttonId ? { buttonId } : {}) });
    },
    [queue],
  );

  const attach = useCallback(
    (file: Blob, kind: "image" | "audio", caption = "") => {
      const preview = kind === "image" ? URL.createObjectURL(file) : undefined;
      queue({ kind, body: caption.trim(), file, ...(preview ? { preview } : {}) });
    },
    [queue],
  );

  return {
    online,
    messages,
    pending,
    problem,
    waiting: online && awaitingReply(messages, pending),
    say,
    attach,
  };
}
