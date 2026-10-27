import { describe, expect, it } from "vitest";
import type { ServerMessage } from "./api";
import { awaitingReply, lastSeen, linkify, liveReplyId, merge, settle } from "./chat";
import type { Outgoing } from "./chat";
import { clock, voiceType } from "./media";

const msg = (
  id: string,
  direction: "in" | "out",
  body: string,
  at: string,
  clientId: string | null = null,
): ServerMessage => ({
  id,
  direction,
  kind: "text",
  body,
  payload: {},
  client_id: clientId,
  created_at: `2026-10-27T10:00:${at}Z`,
});

const typed = (clientId: string, body = "salam"): Outgoing => ({ clientId, kind: "text", body });

describe("chat helpers", () => {
  it("merges polled messages without duplicates, in time order", () => {
    const a = msg("a", "in", "salam", "01");
    const b = msg("b", "out", "wa alaikum", "02");
    const c = msg("c", "in", "price?", "03");
    expect(merge([a, b], [b, c]).map((m) => m.id)).toEqual(["a", "b", "c"]);
    expect(merge([c], [a]).map((m) => m.id)).toEqual(["a", "c"]);
  });

  it("settles each sent message by the id the page gave it, even with the same words", () => {
    const pending = [typed("1"), typed("2"), typed("3", "price?")];
    const server = [msg("a", "in", "salam", "01", "2"), msg("b", "out", "price?", "02")];
    expect(settle(pending, server).map((p) => p.clientId)).toEqual(["1", "3"]);
  });

  it("knows when the shop owes a reply, and which reply can still be tapped", () => {
    const asked = [msg("a", "in", "salam", "01")];
    const answered = [...asked, msg("b", "out", "ji", "02")];
    expect(awaitingReply(asked, [])).toBe(true);
    expect(awaitingReply(answered, [])).toBe(false);
    expect(awaitingReply(answered, [typed("x")])).toBe(true);
    expect(lastSeen(answered)).toBe(answered[1]?.created_at);
    expect(lastSeen([])).toBeUndefined();
    expect(liveReplyId(answered)).toBe("b");
    expect(liveReplyId(asked)).toBeUndefined();
  });

  it("finds links, and marks the owner console link", () => {
    const made =
      "Your shop is ready: https://chatfront.test/s/noor-fabrics.\n" +
      "Manage it here: https://chatfront.test/owner#t=abc.def";
    expect(linkify(made)).toEqual([
      { text: "Your shop is ready: " },
      { href: "https://chatfront.test/s/noor-fabrics", console: false },
      { text: ".\nManage it here: " },
      { href: "https://chatfront.test/owner#t=abc.def", console: true },
    ]);
    expect(linkify("no links")).toEqual([{ text: "no links" }]);
    expect(linkify("")).toEqual([]);
  });
});

describe("voice notes", () => {
  it("record in a format the server reads, or not at all", () => {
    expect(voiceType(() => true)).toBe("audio/webm;codecs=opus");
    expect(voiceType((t) => t.startsWith("audio/ogg"))).toBe("audio/ogg;codecs=opus");
    expect(voiceType((t) => t === "audio/mp4")).toBeUndefined();
    expect(clock(75)).toBe("1:15");
  });
});
