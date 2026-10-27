import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { ServerMessage } from "../api";
import { awaitingReply, lastSeen, merge, settle } from "../chat";
import type { Pending } from "../chat";
import { Avatar } from "../components/Avatar";
import { BackIcon, CheckIcon, ClockIcon, SendIcon } from "../components/Icons";
import { LangSelect } from "../components/Shell";
import { WakeBar } from "../components/WakeBar";
import { scriptLang } from "../i18n";
import { useLang } from "../lang";
import { useServer } from "../server";
import { useShop } from "./ShopPage";

const POLL_MS = 1500;
const SUGGESTIONS = ["chat.suggest_1", "chat.suggest_2", "chat.suggest_3", "chat.suggest_4"] as const;

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

function timeOf(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function ChatPage() {
  const { slug = "" } = useParams();
  const { t } = useLang();
  const { state } = useServer();
  const online = state === "online";
  const { profile } = useShop(slug);
  const [token, setToken] = useState<string | null>(() => storedToken(slug));
  const [messages, setMessages] = useState<ServerMessage[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [sent, setSent] = useState<Set<string>>(() => new Set());
  const [draft, setDraft] = useState("");
  const [failed, setFailed] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  const ensureToken = useCallback(async () => {
    if (token) return token;
    const { token: fresh } = await api.startChat(slug);
    storeToken(slug, fresh);
    setToken(fresh);
    return fresh;
  }, [slug, token]);

  // Poll for new messages once the server is awake.
  useEffect(() => {
    if (!token || !online) return;
    let live = true;
    let after: string | undefined;
    const poll = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const fresh = await api.listChat(slug, token, after);
        if (!live || fresh.length === 0) return;
        after = lastSeen(fresh);
        setMessages((current) => merge(current, fresh));
      } catch {
        // A missed poll is retried on the next tick.
      }
    };
    void poll();
    const timer = window.setInterval(poll, POLL_MS);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [slug, token, online]);

  useEffect(() => {
    setPending((typed) => settle(typed, messages));
  }, [messages]);

  // Send whatever was typed while the server was asleep, in order, once it wakes.
  useEffect(() => {
    if (!online) return;
    const unsent = pending.filter((p) => !sent.has(p.clientId));
    if (unsent.length === 0) return;
    let cancelled = false;
    (async () => {
      try {
        const current = await ensureToken();
        for (const typed of unsent) {
          if (cancelled) return;
          await api.postChat(slug, { token: current, client_id: typed.clientId, text: typed.body });
          setSent((done) => new Set(done).add(typed.clientId));
        }
      } catch {
        setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [online, pending, sent, slug, ensureToken]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, pending]);

  function say(text: string) {
    const body = text.trim();
    if (!body) return;
    setFailed(false);
    setPending((current) => [...current, { clientId: crypto.randomUUID(), body }]);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    say(draft);
    setDraft("");
  }

  const name = profile?.name ?? "";
  const waiting = online && awaitingReply(messages, pending);
  const empty = messages.length === 0 && pending.length === 0;

  return (
    <div className="chat-stage">
      <section className="phone" aria-label={t("chat.title", { shop: name })}>
        <header className="phone-bar">
          <Link to={`/s/${slug}`} className="phone-back" aria-label={t("chat.back")}>
            <BackIcon className="flip-rtl" />
          </Link>
          {name && <Avatar name={name} template={profile?.template} size="sm" />}
          <span className="phone-who">
            <span className="phone-title" lang={scriptLang(name)}>
              {name}
            </span>
            <span className={`phone-status ${online ? "is-online" : ""}`}>
              {online ? t("chat.online") : t("chat.waking")}
            </span>
          </span>
          <LangSelect />
        </header>
        <ol className="thread" aria-live="polite">
          <li className="thread-note">{t("chat.demo_note")}</li>
          {empty && <li className="thread-empty">{t("chat.empty")}</li>}
          {messages.map((message) => (
            <li key={message.id} className={`bubble bubble-${message.direction}`}>
              {message.direction === "in" && <span className="sr-only">{t("chat.you")}: </span>}
              <span dir="auto">{message.body}</span>
              {message.payload.buttons && (
                <span className="bubble-buttons">
                  {message.payload.buttons.map((b) => (
                    <button key={b.id} type="button" onClick={() => say(b.title)}>
                      {b.title}
                    </button>
                  ))}
                </span>
              )}
              <span className="bubble-meta">
                {timeOf(message.created_at)}
                {message.direction === "in" && <CheckIcon size={14} />}
              </span>
            </li>
          ))}
          {pending.map((typed) => (
            <li key={typed.clientId} className="bubble bubble-in bubble-pending">
              <span dir="auto">{typed.body}</span>
              <span className="bubble-meta">
                {!online && <span>{t("chat.queued")}</span>}
                <ClockIcon size={14} />
              </span>
            </li>
          ))}
          {waiting && (
            <li className="typing" aria-label={t("chat.typing")}>
              <span />
              <span />
              <span />
            </li>
          )}
        </ol>
        <div ref={endRef} />
        {failed && (
          <p className="phone-error" role="alert">
            {t("chat.error")}
          </p>
        )}
        <WakeBar placement="inline" />
        {empty && (
          <div className="suggestions">
            {SUGGESTIONS.map((key) => (
              <button key={key} type="button" onClick={() => say(t(key))}>
                {t(key)}
              </button>
            ))}
          </div>
        )}
        <form className="composer" onSubmit={submit}>
          <label className="sr-only" htmlFor="chat-input">
            {t("chat.placeholder")}
          </label>
          <input
            id="chat-input"
            dir="auto"
            autoComplete="off"
            maxLength={2000}
            value={draft}
            placeholder={t("chat.placeholder")}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="submit" disabled={!draft.trim()} aria-label={t("chat.send")}>
            <SendIcon className="flip-rtl" />
          </button>
        </form>
        <p className="phone-credit">{t("app.attribution")}</p>
      </section>
    </div>
  );
}
