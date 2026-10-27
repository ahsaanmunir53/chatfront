import { describe, expect, it } from "vitest";
import en from "@i18n/en.json";
import ur from "@i18n/ur.json";
import roman from "@i18n/ur-Latn.json";
import { dirOf, initialLang, pick, translate } from "./i18n";

describe("i18n", () => {
  it("has every key in Urdu and Roman Urdu", () => {
    expect(Object.keys(ur).sort()).toEqual(Object.keys(en).sort());
    expect(Object.keys(roman).sort()).toEqual(Object.keys(en).sort());
  });

  it("fills values and keeps unknown placeholders visible", () => {
    expect(translate("en", "chat.title", { shop: "Saffron" })).toBe("Try it: chat with Saffron");
    expect(translate("ur-Latn", "chat.title", { shop: "Saffron" })).toContain("Saffron");
    expect(translate("en", "chat.title")).toBe("Try it: chat with {shop}");
  });

  it("reads Urdu right to left only", () => {
    expect(dirOf("ur")).toBe("rtl");
    expect(dirOf("ur-Latn")).toBe("ltr");
    expect(dirOf("en")).toBe("ltr");
  });

  it("picks the language, falling back to English", () => {
    expect(pick({ en: "Lahore", ur: "لاہور" }, "ur")).toBe("لاہور");
    expect(pick({ en: "Lahore" }, "ur-Latn")).toBe("Lahore");
    expect(pick(undefined, "en")).toBe("");
  });

  it("starts in the saved language, else Urdu for Urdu browsers, else English", () => {
    expect(initialLang("ur-Latn", "en-US")).toBe("ur-Latn");
    expect(initialLang("klingon", "ur-PK")).toBe("ur");
    expect(initialLang(null, "en-GB")).toBe("en");
  });
});
