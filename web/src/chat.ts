// Pure helpers for the try-it chat: merging polled messages and settling what was typed.
import type { ServerMessage } from "./api";

export type Pending = { clientId: string; body: string };

/** Add newly polled messages, without duplicates, in the order they were written. */
export function merge(existing: ServerMessage[], incoming: ServerMessage[]): ServerMessage[] {
  const byId = new Map(existing.map((m) => [m.id, m]));
  for (const message of incoming) byId.set(message.id, message);
  return [...byId.values()].sort(
    (a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );
}

/** Drop typed messages the server now has; each server copy settles one typed message. */
export function settle(pending: Pending[], messages: ServerMessage[]): Pending[] {
  const arrived = new Map<string, number>();
  for (const m of messages) {
    if (m.direction === "in" && m.body) arrived.set(m.body, (arrived.get(m.body) ?? 0) + 1);
  }
  return pending.filter((p) => {
    const count = arrived.get(p.body) ?? 0;
    if (count === 0) return true;
    arrived.set(p.body, count - 1);
    return false;
  });
}

export function lastSeen(messages: ServerMessage[]): string | undefined {
  return messages.at(-1)?.created_at;
}

/** The shop owes a reply when something is still sending or the customer spoke last. */
export function awaitingReply(messages: ServerMessage[], pending: Pending[]): boolean {
  return pending.length > 0 || messages.at(-1)?.direction === "in";
}
