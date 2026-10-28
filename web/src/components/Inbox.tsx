// The unified inbox (feature 76): every customer chat from WhatsApp, Instagram, Messenger and
// the web chat, newest first, with who has it. A person takes a chat (the assistant stops
// there), the owner gives one to someone, and either gives it back to the assistant. The chat
// reads in the reader's own language; replies go out in the customer's.
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type { ConsoleMessage, InboxItem, InboxShow, OwnerMe } from "../api";
import type { Key, Values } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 4000;
const SHOWS: InboxShow[] = ["all", "waiting", "mine"];
const CHANNELS = ["whatsapp", "instagram", "messenger", "webchat"] as const;
const BY: Record<ConsoleMessage["by"], Key> = {
  customer: "console.customer_label",
  agent: "console.assistant_label",
  staff: "console.you",
};

function channelName(t: (key: Key) => string, channel: string): string {
  return t(`console.dash_source.${channel}` as Key);
}

function useWhen() {
  const { lang } = useLang();
  return useCallback(
    (iso: string) => {
      const at = new Date(iso);
      const today = at.toDateString() === new Date().toDateString();
      return new Intl.DateTimeFormat(
        lang,
        today ? { hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short" },
      ).format(at);
    },
    [lang],
  );
}

function Thread({ token, item }: { token: string; item: InboxItem }) {
  const { t } = useLang();
  const when = useWhen();
  const [messages, setMessages] = useState<ConsoleMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [failed, setFailed] = useState(false);
  const chatId = item.conversation_id;
  const load = useCallback(() => {
    api.chatMessages(token, chatId).then(setMessages, () => {
      // The next poll tries again.
    });
  }, [token, chatId]);

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
      await api.staffReply(token, chatId, text);
      setDraft("");
      load();
    } catch {
      setFailed(true);
    }
  }

  const canReply = item.state !== "assistant" && item.window_open;
  return (
    <div className="inbox-chat">
      <ol className="inbox-thread" aria-label={t("console.inbox_thread")}>
        {messages.map((message) => (
          <li key={message.id} className={`inbox-line by-${message.by}`}>
            <span className="inbox-who">
              {t(BY[message.by])}{" "}
              <time dateTime={message.created_at}>{when(message.created_at)}</time>
            </span>
            <span dir="auto">{message.body ?? `[${message.kind}]`}</span>
            {message.original && (
              <span className="inbox-original" dir="auto">
                {t("console.their_words", { text: message.original })}
              </span>
            )}
          </li>
        ))}
      </ol>
      {item.state === "assistant" ? (
        <p className="inbox-note">{t("console.inbox_take_to_reply")}</p>
      ) : !item.window_open ? (
        <p className="inbox-note">{t("console.inbox_window_closed")}</p>
      ) : null}
      {canReply && (
        <form className="inbox-reply" onSubmit={(event) => void send(event)}>
          <label className="sr-only" htmlFor={`reply-${chatId}`}>
            {t("console.reply_placeholder")}
          </label>
          <textarea
            id={`reply-${chatId}`}
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
      )}
    </div>
  );
}

function stateText(t: (key: Key, values?: Values) => string, item: InboxItem) {
  if (item.state === "assistant") return t("console.inbox_state_assistant");
  if (item.state === "waiting") return t("console.inbox_state_waiting");
  return t("console.inbox_state_person", { name: item.assigned?.name ?? "" });
}

function OpenChat({
  me,
  token,
  item,
  people,
  canAssign,
  onChange,
  onClose,
}: {
  me: OwnerMe;
  token: string;
  item: InboxItem;
  people: { id: string; name: string }[];
  canAssign: boolean;
  onChange: () => void;
  onClose: () => void;
}) {
  const { t } = useLang();
  const [busy, setBusy] = useState(false);
  const mine = item.assigned?.id === me.staff_id;
  const elsewhere = item.known_on.filter((channel) => channel !== item.channel);

  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    await run().catch(() => undefined);
    setBusy(false);
    onChange();
  }

  return (
    <article className="inbox-open" aria-labelledby="inbox-open-title">
      <header className="inbox-open-head">
        <button type="button" className="action inbox-back" onClick={onClose}>
          {t("console.inbox_back")}
        </button>
        <h3 id="inbox-open-title" dir="auto">
          {item.customer ?? t("console.customer_label")}
        </h3>
        <p className="inbox-open-meta">
          {channelName(t, item.channel)}
          {elsewhere.length > 0 && (
            <span>
              {" "}
              {t("console.inbox_also_on", {
                channels: elsewhere.map((c) => channelName(t, c)).join(", "),
              })}
            </span>
          )}
        </p>
        <p className={`inbox-state is-${item.state}`}>{stateText(t, item)}</p>
      </header>
      {item.summary && (
        <p className="inbox-summary" dir="auto">
          {item.summary}
        </p>
      )}
      <div className="inbox-actions">
        {!mine && (
          <button
            type="button"
            className="action action-primary"
            disabled={busy}
            onClick={() => void act(() => api.takeChat(token, item.conversation_id))}
          >
            {t("console.inbox_take")}
          </button>
        )}
        {canAssign && (
          <label className="inbox-give">
            <span>{t("console.inbox_give_to")}</span>
            <select
              value={item.assigned?.id ?? ""}
              disabled={busy}
              onChange={(e) =>
                void act(() => api.giveChat(token, item.conversation_id, e.target.value || null))
              }
            >
              <option value="">{t("console.inbox_nobody")}</option>
              {people.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {item.handover_id && (
          <button
            type="button"
            className="action"
            disabled={busy}
            onClick={() =>
              void act(() => api.resumeHandover(token, item.handover_id ?? "")).then(onClose)
            }
          >
            {t("handover.resume")}
          </button>
        )}
      </div>
      <Thread token={token} item={item} />
    </article>
  );
}

export function InboxPanel({ me, token }: { me: OwnerMe; token: string }) {
  const { t } = useLang();
  const when = useWhen();
  const [show, setShow] = useState<InboxShow>("all");
  const [channel, setChannel] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);
  const load = useCallback(() => api.inbox(token, show, channel), [token, show, channel]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const items = data?.items ?? [];
  const open = items.find((item) => item.conversation_id === openId) ?? null;

  return (
    <section
      className={`inbox ${open ? "has-open" : ""}`}
      aria-labelledby="inbox-title"
      aria-busy={!data}
    >
      <header className="inbox-head">
        <h2 id="inbox-title" className="inbox-title">
          {t("console.inbox_title")}
        </h2>
        {(data?.waiting ?? 0) > 0 && (
          <span className="inbox-waiting">
            {t("console.inbox_waiting_count", { count: data?.waiting ?? 0 })}
          </span>
        )}
      </header>
      <div className="inbox-filters">
        <div className="inbox-shows" role="group" aria-label={t("console.inbox_title")}>
          {SHOWS.map((name) => (
            <button
              key={name}
              type="button"
              aria-pressed={show === name}
              onClick={() => setShow(name)}
            >
              {t(`console.inbox_${name}` as Key)}
            </button>
          ))}
        </div>
        <label className="inbox-channel">
          <span className="sr-only">{t("console.inbox_channel")}</span>
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="">{t("console.inbox_all_channels")}</option>
            {CHANNELS.map((name) => (
              <option key={name} value={name}>
                {channelName(t, name)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="inbox-grid">
        {items.length === 0 ? (
          <p className="inbox-empty">{data ? t("console.inbox_empty") : ""}</p>
        ) : (
          <ul className="inbox-list">
            {items.map((item) => (
              <li key={item.conversation_id}>
                <button
                  type="button"
                  className={`inbox-row is-${item.state}`}
                  aria-current={item.conversation_id === openId || undefined}
                  onClick={() => setOpenId(item.conversation_id)}
                >
                  <span className="inbox-row-head">
                    <strong dir="auto">{item.customer ?? t("console.customer_label")}</strong>
                    <span className="inbox-row-channel">{channelName(t, item.channel)}</span>
                    {item.last && (
                      <time dateTime={item.last.at} className="inbox-row-time">
                        {when(item.last.at)}
                      </time>
                    )}
                  </span>
                  {item.last && (
                    <span className="inbox-row-last" dir="auto">
                      {item.last.by !== "customer" && <span>{t(BY[item.last.by])}: </span>}
                      {item.last.text ?? `[${item.last.kind}]`}
                    </span>
                  )}
                  <span className={`inbox-state is-${item.state}`}>{stateText(t, item)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {open && data && (
          <OpenChat
            key={open.conversation_id}
            me={me}
            token={token}
            item={open}
            people={data.people}
            canAssign={data.can_assign}
            onChange={refresh}
            onClose={() => setOpenId(null)}
          />
        )}
      </div>
    </section>
  );
}
