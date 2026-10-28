import { beforeEach, describe, expect, it } from "vitest";
import { MemoryStorage } from "./testing";
import { cleanCode, forgetGroup, groupLink, readGroup, readRef, rememberVisit } from "./visit";

describe("a shared link's codes", () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, "sessionStorage", {
      value: new MemoryStorage(),
      configurable: true,
    });
  });

  it("keeps only codes the API takes", () => {
    expect(cleanCode(" rab 12c ")).toBe("RAB12C");
    expect(cleanCode("G7")).toBeNull();
    expect(cleanCode("<script>")).toBeNull();
    expect(cleanCode(null)).toBeNull();
  });

  it("remembers a friend's code and a group per shop until the group is left", () => {
    rememberVisit("gulmohar", new URLSearchParams("ref=rab12c&group=gxy7k2"));
    expect(readRef("gulmohar")).toBe("RAB12C");
    expect(readGroup("gulmohar")).toBe("GXY7K2");
    expect(readRef("barkat")).toBeNull();
    forgetGroup("gulmohar");
    expect(readGroup("gulmohar")).toBeNull();
    expect(readRef("gulmohar")).toBe("RAB12C");
  });

  it("builds the group page's link under the app's base path", () => {
    expect(groupLink("https://x.test", "/", "gulmohar", "GXY7K2")).toBe(
      "https://x.test/s/gulmohar/g/GXY7K2",
    );
    expect(groupLink("https://x.test", "/app/", "gulmohar", "GXY7K2")).toBe(
      "https://x.test/app/s/gulmohar/g/GXY7K2",
    );
  });
});
