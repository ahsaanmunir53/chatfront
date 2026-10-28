// Pure helpers for the chats: merging polled messages, settling what was sent, links in text.
import type { Coordinates, ServerMessage } from "./api";

/** Something typed, tapped or attached here that the server has not shown back yet. */
export type Outgoing = {
  clientId: string;
  kind: "text" | "image" | "video" | "audio" | "location";
  body: string; // the text, a photo's or video's caption, or empty for a voice note
  buttonId?: string;
  location?: Coordinates;
  file?: Blob;
  preview?: string; // an object URL for a photo or video being sent
};

/** Add newly polled messages, without duplicates, in the order they were written. */
export function merge(existing: ServerMessage[], incoming: ServerMessage[]): ServerMessage[] {
  const byId = new Map(existing.map((m) => [m.id, m]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort(
    (a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );
}

/** Drop what the server now has: it returns the id the page gave each message it sent. */
export function settle(pending: Outgoing[], messages: ServerMessage[]): Outgoing[] {
  const arrived = new Set(messages.map((m) => m.client_id).filter(Boolean));
  return pending.filter((p) => !arrived.has(p.clientId));
}

export function lastSeen(messages: ServerMessage[]): string | undefined {
  return messages.at(-1)?.created_at;
}

/** The shop owes a reply when something is still sending or the customer spoke last. */
export function awaitingReply(messages: ServerMessage[], pending: Outgoing[]): boolean {
  return pending.length > 0 || messages.at(-1)?.direction === "in";
}

/** Only the newest reply's buttons can be tapped; older cards are settled or replaced. */
export function liveReplyId(messages: ServerMessage[]): string | undefined {
  const last = messages.at(-1);
  return last?.direction === "out" ? last.id : undefined;
}

export type Piece = { text: string } | { href: string; console: boolean };

const URL_PATTERN = /https?:\/\/[^\s<>"]+[^\s<>".,;:!?)'’”]/g;

/** Text with its web links found, so they can be drawn as links. The owner console link
 * (…/owner#t=…) is marked: it signs the owner in and is shown as a button, not a token. */
export function linkify(text: string): Piece[] {
  const pieces: Piece[] = [];
  let at = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const start = match.index ?? 0;
    if (start > at) pieces.push({ text: text.slice(at, start) });
    const href = match[0];
    pieces.push({ href, console: /\/owner#t=/.test(href) });
    at = start + href.length;
  }
  if (at < text.length) pieces.push({ text: text.slice(at) });
  return pieces;
}

/** A map link for a shared location. */
export function mapLink({ latitude, longitude }: Coordinates): string {
  return `https://maps.google.com/?q=${latitude.toFixed(6)},${longitude.toFixed(6)}`;
}
