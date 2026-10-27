import { describe, expect, it } from "vitest";
import type { ServerMessage } from "./api";
import { awaitingReply, lastSeen, merge, settle } from "./chat";

const msg = (id: string, direction: "in" | "out", body: string, at: string): ServerMessage => ({
  id,
  direction,
  kind: "text",
  body,
  payload: {},
  created_at: `2026-10-27T10:00:${at}Z`,
});

describe("chat helpers", () => {
  it("merges polled messages without duplicates, in time order", () => {
    const a = msg("a", "in", "salam", "01");
    const b = msg("b", "out", "wa alaikum", "02");
    const c = msg("c", "in", "price?", "03");
    expect(merge([a, b], [b, c]).map((m) => m.id)).toEqual(["a", "b", "c"]);
    expect(merge([c], [a]).map((m) => m.id)).toEqual(["a", "c"]);
  });

  it("settles each typed message once the server has it", () => {
    const pending = [
      { clientId: "1", body: "salam" },
      { clientId: "2", body: "salam" },
      { clientId: "3", body: "price?" },
    ];
    const server = [msg("a", "in", "salam", "01"), msg("b", "out", "price?", "02")];
    expect(settle(pending, server).map((p) => p.clientId)).toEqual(["2", "3"]);
  });

  it("knows when the shop owes a reply", () => {
    const asked = [msg("a", "in", "salam", "01")];
    const answered = [...asked, msg("b", "out", "ji", "02")];
    expect(awaitingReply(asked, [])).toBe(true);
    expect(awaitingReply(answered, [])).toBe(false);
    expect(awaitingReply(answered, [{ clientId: "x", body: "hi" }])).toBe(true);
    expect(lastSeen(answered)).toBe(answered[1]?.created_at);
    expect(lastSeen([])).toBeUndefined();
  });
});
