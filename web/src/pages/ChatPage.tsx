import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import type { ServerMessage } from "../api";
import { awaitingReply, lastSeen, merge, settle } from "../chat";
import type { Pending } from "../chat";
import { LangSwitch } from "../components/Shell";
import { scriptLang } from "../i18n";
import { useLang } from "../lang";
import { useLoad } from "../useLoad";

const POLL_MS = 1500;

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

export function ChatPage() {
  const { slug = "" } = useParams();
  const { t } = useLang();
  const shop = useLoad(() => api.shop(slug), slug);
  const [token, setToken] = useState<string | null>(() => storedToken(slug));
  const [messages, setMessages] = useState<ServerMessage[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
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

  useEffect(() => {
    if (!token) return;
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
  }, [slug, token]);

  useEffect(() => {
    setPending((typed) => settle(typed, messages));
  }, [messages]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, pending]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    const typed = { clientId: crypto.randomUUID(), body: text };
    setDraft("");
    setFailed(false);
    setPending((current) => [...current, typed]);
    try {
      const current = await ensureToken();
      await api.postChat(slug, { token: current, client_id: typed.clientId, text });
    } catch {
      setPending((current) => current.filter((p) => p.clientId !== typed.clientId));
      setDraft(text);
      setFailed(true);
    }
  }

  const name = shop.state === "ready" ? shop.data.name : "";
  const waiting = awaitingReply(messages, pending);

  return (
    <div className="chat-stage">
      <section className="phone" aria-label={t("chat.title", { shop: name })}>
        <header className="phone-bar">
          <Link to={`/s/${slug}`} className="phone-back">
            {t("chat.back")}
          </Link>
          <h1 className="phone-title" lang={scriptLang(name)}>
            {name}
          </h1>
          <LangSwitch />
        </header>
        <p className="phone-note">{t("chat.demo_note")}</p>
        <ol className="thread" aria-live="polite">
          {messages.length === 0 && pending.length === 0 && (
            <li className="thread-empty">{t("chat.empty")}</li>
          )}
          {messages.map((message) => (
            <li key={message.id} className={`bubble bubble-${message.direction}`}>
              {message.direction === "in" && <span className="sr-only">{t("chat.you")}: </span>}
              <span dir="auto">{message.body}</span>
              {message.payload.buttons && (
                <span className="bubble-buttons">
                  {message.payload.buttons.map((b) => (
                    <span key={b.id} className="bubble-button">
                      {b.title}
                    </span>
                  ))}
                </span>
              )}
            </li>
          ))}
          {pending.map((typed) => (
            <li key={typed.clientId} className="bubble bubble-in bubble-sending">
              <span dir="auto">{typed.body}</span>
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
        <form className="composer" onSubmit={send}>
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
          <button type="submit" disabled={!draft.trim()}>
            {t("chat.send")}
          </button>
        </form>
        <p className="phone-credit">{t("app.attribution")}</p>
      </section>
    </div>
  );
}
