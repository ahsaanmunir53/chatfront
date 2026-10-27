// Customers the assistant handed to the owner (feature 90): the three-line summary, the chat
// in the owner's own language, a reply box, and a button that gives the chat back.
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type { ConsoleMessage, Handover } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { useServer } from "../server";

const POLL_MS = 4000;
const CHANNELS: Record<string, string> = {
  whatsapp: "WhatsApp",
  instagram: "Instagram",
  messenger: "Messenger",
  webchat: "Web",
};
const BY: Record<ConsoleMessage["by"], Key> = {
  customer: "console.customer_label",
  agent: "console.assistant_label",
  staff: "console.you",
};

/** Polls the open handovers; refresh() after a change. */
export function useHandovers(token: string) {
  const { state } = useServer();
  const [list, setList] = useState<Handover[]>([]);
  const refresh = useCallback(() => {
    api.handovers(token).then(setList, () => {
      // The next poll tries again.
    });
  }, [token]);
  useEffect(() => {
    if (state !== "online") return;
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [state, refresh]);
  return { list, refresh };
}

function timeOf(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function Conversation({ token, handover }: { token: string; handover: Handover }) {
  const { t } = useLang();
  const [messages, setMessages] = useState<ConsoleMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [failed, setFailed] = useState(false);
  const load = useCallback(() => {
    api.handoverMessages(token, handover.conversation_id).then(setMessages, () => {
      // The next poll tries again.
    });
  }, [token, handover.conversation_id]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setFailed(false);
    try {
      await api.staffReply(token, handover.conversation_id, text);
      setDraft("");
      load();
    } catch {
      setFailed(true);
    }
  }

  return (
    <div className="waiting-chat">
      <ol className="waiting-thread">
        {messages.map((message) => (
          <li key={message.id} className={`waiting-line by-${message.by}`}>
            <span className="waiting-who">
              {t(BY[message.by])} <time>{timeOf(message.created_at)}</time>
            </span>
            <span dir="auto">{message.body ?? `[${message.kind}]`}</span>
            {message.original && (
              <span className="waiting-original" dir="auto">
                {t("console.their_words", { text: message.original })}
              </span>
            )}
          </li>
        ))}
      </ol>
      <form className="waiting-reply" onSubmit={(event) => void send(event)}>
        <label className="sr-only" htmlFor={`reply-${handover.id}`}>
          {t("console.reply_placeholder")}
        </label>
        <textarea
          id={`reply-${handover.id}`}
          dir="auto"
          rows={2}
          maxLength={2000}
          value={draft}
          placeholder={t("console.reply_placeholder")}
          onChange={(e) => setDraft(e.target.value)}
        />
        <button type="submit" className="button button-ink button-small" disabled={!draft.trim()}>
          {t("chat.send")}
        </button>
        {failed && <span role="alert">{t("console.reply_failed")}</span>}
      </form>
    </div>
  );
}

export function Waiting({
  token,
  list,
  onChange,
}: {
  token: string;
  list: Handover[];
  onChange: () => void;
}) {
  const { t } = useLang();
  const [open, setOpen] = useState<string | null>(null);
  if (list.length === 0) return null;

  async function giveBack(handover: Handover) {
    await api.resumeHandover(token, handover.id).catch(() => undefined);
    setOpen(null);
    onChange();
  }

  return (
    <section className="waiting" aria-labelledby="waiting-title">
      <h2 id="waiting-title" className="waiting-title">
        {t("console.waiting_title")}
        <span className="console-count">{list.length}</span>
      </h2>
      <ul className="waiting-list">
        {list.map((handover) => (
          <li key={handover.id} className="waiting-item">
            <p className="waiting-head">
              <strong dir="auto">{handover.customer ?? t("console.customer_label")}</strong>
              <span lang="en">{CHANNELS[handover.channel] ?? handover.channel}</span>
              <time>{timeOf(handover.created_at)}</time>
            </p>
            <p className="waiting-summary" dir="auto">
              {handover.summary}
            </p>
            <div className="waiting-actions">
              <button
                type="button"
                className="action"
                aria-expanded={open === handover.id}
                onClick={() => setOpen(open === handover.id ? null : handover.id)}
              >
                {open === handover.id ? t("console.close_chat") : t("console.open_chat")}
              </button>
              <button
                type="button"
                className="action action-primary"
                onClick={() => void giveBack(handover)}
              >
                {t("handover.resume")}
              </button>
            </div>
            {open === handover.id && <Conversation token={token} handover={handover} />}
          </li>
        ))}
      </ul>
    </section>
  );
}
