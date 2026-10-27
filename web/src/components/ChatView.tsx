// The chat itself, shared by the try-it chat, the start page and the owner console: a thread
// of bubbles (text, photos, voice notes, reply buttons and lists) and a composer that sends
// text, a photo with the typed words as its caption, or a voice note.
import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import { Link } from "react-router-dom";
import type { ServerMessage } from "../api";
import { linkify, liveReplyId, mapLink } from "../chat";
import type { Outgoing } from "../chat";
import { dirOf, scriptLang } from "../i18n";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { MAX_VOICE_SECONDS, clock, shrinkImage, useRecorder } from "../media";
import { useChat } from "../useChat";
import type { SendProblem } from "../useChat";
import { Avatar } from "./Avatar";
import {
  BackIcon,
  CameraIcon,
  CheckIcon,
  ClockIcon,
  CloseIcon,
  KeyIcon,
  MicIcon,
  PinIcon,
  SendIcon,
} from "./Icons";
import { LangSelect } from "./Shell";
import { WakeBar } from "./WakeBar";

const PROBLEMS: Record<SendProblem, Key> = {
  network: "chat.error",
  type: "chat.not_sent_type",
  size: "chat.not_sent_size",
  signed_out: "console.expired",
};

function timeOf(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Message text with its links drawn; the owner console link becomes a sign-in button. */
function Linked({ text }: { text: string }) {
  const { t } = useLang();
  return (
    <>
      {linkify(text).map((piece, index) =>
        "text" in piece ? (
          <Fragment key={index}>{piece.text}</Fragment>
        ) : piece.console ? (
          <a key={index} href={piece.href} className="bubble-console">
            <KeyIcon size={16} />
            {t("chat.open_console")}
          </a>
        ) : (
          <a key={index} href={piece.href} dir="ltr">
            {piece.href}
          </a>
        ),
      )}
    </>
  );
}

function Media({ kind, url }: { kind: string; url: string | null | undefined }) {
  const { t } = useLang();
  if (!url) return null;
  if (kind === "image") {
    return <img className="bubble-photo" src={url} alt={t("chat.photo")} loading="lazy" />;
  }
  if (kind === "audio") {
    return (
      <audio className="bubble-audio" controls preload="none" src={url}>
        {t("chat.voice_note")}
      </audio>
    );
  }
  return null;
}

function Bubble({
  message,
  live,
  onTap,
  onShareLocation,
}: {
  message: ServerMessage;
  live: boolean;
  onTap: (title: string, id: string) => void;
  onShareLocation: () => void;
}) {
  const { t } = useLang();
  const { buttons, list, location, location_request: askLocation } = message.payload;
  const voice = message.kind === "audio" && !message.body;
  return (
    <li className={`bubble bubble-${message.direction}`}>
      {message.direction === "in" && <span className="sr-only">{t("chat.you")}: </span>}
      <Media kind={message.kind} url={message.media_url} />
      {voice && !message.media_url && <span>{t("chat.voice_note")}</span>}
      {message.body && (
        <span lang={message.lang ?? undefined} dir={message.lang ? dirOf(message.lang) : "auto"}>
          <Linked text={message.body} />
        </span>
      )}
      {location && (
        <a className="bubble-pin" href={mapLink(location)} target="_blank" rel="noopener" dir="ltr">
          <PinIcon size={16} />
          {location.latitude.toFixed(5)}, {location.longitude.toFixed(5)}
        </a>
      )}
      {askLocation && (
        <span className="bubble-buttons">
          <button type="button" disabled={!live} onClick={onShareLocation}>
            <PinIcon size={16} />
            {t("chat.share_location")}
          </button>
        </span>
      )}
      {buttons && buttons.length > 0 && (
        <span className="bubble-buttons">
          {buttons.map((b) => (
            <button key={b.id} type="button" disabled={!live} onClick={() => onTap(b.title, b.id)}>
              {b.title}
            </button>
          ))}
        </span>
      )}
      {list && (
        <span className="bubble-list" role="group" aria-label={list.button}>
          {list.rows.map((row) => (
            <button
              key={row.id}
              type="button"
              disabled={!live}
              onClick={() => onTap(row.title, row.id)}
            >
              <span className="bubble-list-title">{row.title}</span>
              {row.description && <span className="bubble-list-note">{row.description}</span>}
            </button>
          ))}
        </span>
      )}
      <span className="bubble-meta">
        {timeOf(message.created_at)}
        {message.direction === "in" && <CheckIcon size={14} />}
      </span>
    </li>
  );
}

function PendingBubble({ item, online }: { item: Outgoing; online: boolean }) {
  const { t } = useLang();
  return (
    <li className="bubble bubble-in bubble-pending">
      {item.preview && <img className="bubble-photo" src={item.preview} alt={t("chat.photo")} />}
      {item.kind === "audio" && <span>{t("chat.voice_note")}</span>}
      {item.body && <span dir="auto">{item.body}</span>}
      <span className="bubble-meta">
        {!online && <span>{t("chat.queued")}</span>}
        <ClockIcon size={14} />
      </span>
    </li>
  );
}

export type ChatViewProps = {
  slug: string;
  label: string;
  title: string;
  avatar?: { name: string; template?: string | undefined };
  back?: { to: string; label: string };
  note: string;
  empty: string;
  suggestions?: string[];
  initialDraft?: string;
  ownerToken?: string | null;
  onSignedOut?: () => void;
  /** Told how many replies have arrived, so a page can refresh what they may have changed. */
  onReplies?: (count: number) => void;
  variant?: "phone" | "panel";
  footer?: ReactNode;
};

export function ChatView({
  slug,
  label,
  title,
  avatar,
  back,
  note,
  empty: emptyText,
  suggestions = [],
  initialDraft = "",
  ownerToken = null,
  onSignedOut,
  onReplies,
  variant = "phone",
  footer,
}: ChatViewProps) {
  const { t } = useLang();
  const chat = useChat(slug, ownerToken, onSignedOut);
  const recorder = useRecorder();
  const [draft, setDraft] = useState(initialDraft);
  const [locationOff, setLocationOff] = useState(false);
  const threadRef = useRef<HTMLOListElement>(null);
  const photoRef = useRef<HTMLInputElement>(null);
  const live = liveReplyId(chat.messages);
  const replies = chat.messages.filter((m) => m.direction === "out").length;
  const recording = recorder.state.phase === "recording" ? recorder.state.seconds : null;

  useEffect(() => {
    const thread = threadRef.current;
    if (thread) thread.scrollTop = thread.scrollHeight;
  }, [chat.messages, chat.pending]);

  useEffect(() => {
    onReplies?.(replies);
  }, [onReplies, replies]);

  const { stop } = recorder;
  const { attach } = chat;
  const sendVoice = useCallback(async () => {
    const blob = await stop();
    if (blob) attach(blob, "audio");
  }, [stop, attach]);

  // A long note is sent when it reaches the limit, not lost.
  useEffect(() => {
    if (recording !== null && recording >= MAX_VOICE_SECONDS) void sendVoice();
  }, [recording, sendVoice]);

  // The browser asks the customer first; a refusal gets a hint to name a landmark instead.
  function shareLocation() {
    setLocationOff(false);
    if (!navigator.geolocation) {
      setLocationOff(true);
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) =>
        chat.share(
          { latitude: coords.latitude, longitude: coords.longitude },
          t("chat.my_location"),
        ),
      () => setLocationOff(true),
      { enableHighAccuracy: true, timeout: 15_000 },
    );
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    chat.say(draft);
    setDraft("");
  }

  async function pickPhoto(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    chat.attach(await shrinkImage(file), "image", draft);
    setDraft("");
  }

  const empty = chat.messages.length === 0 && chat.pending.length === 0;
  const notice =
    recorder.state.phase === "blocked"
      ? t("chat.mic_blocked")
      : recorder.state.phase === "unsupported"
        ? t("chat.no_recording")
        : chat.problem
          ? t(PROBLEMS[chat.problem])
          : locationOff
            ? t("chat.location_off")
            : null;

  return (
    <section className={`phone phone-${variant}`} aria-label={label}>
      <header className="phone-bar">
        {back && (
          <Link to={back.to} className="phone-back" aria-label={back.label}>
            <BackIcon className="flip-rtl" />
          </Link>
        )}
        {avatar?.name && <Avatar name={avatar.name} template={avatar.template} size="sm" />}
        <span className="phone-who">
          <span className="phone-title" lang={scriptLang(title)} dir="auto">
            {title}
          </span>
          <span className={`phone-status ${chat.online ? "is-online" : ""}`}>
            {chat.online ? t("chat.online") : t("chat.waking")}
          </span>
        </span>
        <LangSelect />
      </header>
      <ol className="thread" aria-live="polite" ref={threadRef}>
        <li className="thread-note">{note}</li>
        {empty && <li className="thread-empty">{emptyText}</li>}
        {chat.messages.map((message) => (
          <Bubble
            key={message.id}
            message={message}
            live={message.id === live}
            onTap={(text, id) => chat.say(text, id)}
            onShareLocation={shareLocation}
          />
        ))}
        {chat.pending.map((item) => (
          <PendingBubble key={item.clientId} item={item} online={chat.online} />
        ))}
        {chat.waiting && (
          <li className="typing" aria-label={t("chat.typing")}>
            <span />
            <span />
            <span />
          </li>
        )}
      </ol>
      {notice && (
        <p className="phone-error" role="alert">
          {notice}
        </p>
      )}
      <WakeBar placement="inline" />
      {empty && suggestions.length > 0 && (
        <div className="suggestions">
          {suggestions.map((text) => (
            <button key={text} type="button" onClick={() => chat.say(text)}>
              {text}
            </button>
          ))}
        </div>
      )}
      <form className="composer" onSubmit={submit}>
        {recording !== null ? (
          <>
            <span className="recording" role="status">
              <span className="recording-dot" aria-hidden="true" />
              {t("chat.recording")}
              <span className="recording-time">{clock(recording)}</span>
            </span>
            <button
              type="button"
              className="composer-quiet"
              onClick={recorder.cancel}
              aria-label={t("chat.cancel_voice")}
            >
              <CloseIcon />
            </button>
            <button type="button" onClick={() => void sendVoice()} aria-label={t("chat.send_voice")}>
              <SendIcon className="flip-rtl" />
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="composer-quiet"
              onClick={() => photoRef.current?.click()}
              aria-label={t("chat.send_photo")}
            >
              <CameraIcon />
            </button>
            <input
              ref={photoRef}
              className="sr-only"
              type="file"
              accept="image/*"
              tabIndex={-1}
              aria-hidden="true"
              onChange={(event) => void pickPhoto(event)}
            />
            <label className="sr-only" htmlFor={`chat-input-${slug}`}>
              {t("chat.placeholder")}
            </label>
            <input
              id={`chat-input-${slug}`}
              className="composer-text"
              dir="auto"
              autoComplete="off"
              maxLength={2000}
              value={draft}
              placeholder={t("chat.placeholder")}
              onChange={(e) => {
                setDraft(e.target.value);
                recorder.dismiss();
              }}
            />
            {draft.trim() ? (
              <button type="submit" aria-label={t("chat.send")}>
                <SendIcon className="flip-rtl" />
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void recorder.start()}
                aria-label={t("chat.record")}
              >
                <MicIcon />
              </button>
            )}
          </>
        )}
      </form>
      {footer}
    </section>
  );
}
