import { describe, expect, it } from "vitest";
import cases from "@i18n/icu-cases.json";
import registry from "@i18n/languages.json";
import en from "@i18n/en.json";
import {
  chain,
  dirOf,
  ensureFont,
  fontOf,
  formatMessage,
  initialLang,
  isLoaded,
  loadCatalog,
  localeOf,
  languageOf,
  needsReview,
  pick,
  resolve,
  scriptLang,
  statusOf,
  translate,
  UI_LANGS,
} from "./i18n";
import type { Values } from "./i18n";

type FakeLink = { rel: string; href: string; dataset: Record<string, string> };

/** Just enough of a document for ensureFont: the tests run without a browser. */
function fakeDocument(): { doc: Document; links: FakeLink[] } {
  const links: FakeLink[] = [];
  const doc = {
    head: { append: (link: FakeLink) => links.push(link) },
    createElement: (): FakeLink => ({ rel: "", href: "", dataset: {} }),
    querySelector: (selector: string) =>
      links.find((link) => selector.includes(`"${link.dataset.font}"`)) ?? null,
  };
  return { doc: doc as unknown as Document, links };
}

const catalogs = import.meta.glob<{ default: Record<string, string> }>("@i18n/*.json", {
  eager: true,
});

function catalog(tag: string): Record<string, string> {
  const found = Object.entries(catalogs).find(([path]) => path.endsWith(`/${tag}.json`));
  if (!found) throw new Error(`no catalog for ${tag}`);
  return found[1].default;
}

describe("catalogs", () => {
  it("exist with every key for every interface language", () => {
    const ui = registry.languages.filter((l) => l.ui).map((l) => l.tag);
    expect([...UI_LANGS].sort()).toEqual([...ui].sort());
    for (const tag of ui) expect(Object.keys(catalog(tag))).toEqual(Object.keys(en));
  });

  it("formats ICU messages exactly as the API does", () => {
    expect(cases.cases.length).toBeGreaterThanOrEqual(30);
    for (const c of cases.cases) {
      const values = c.values as Values;
      expect(formatMessage(c.message, c.lang, values), `${c.lang} ${c.message}`).toBe(
        c.expected,
      );
    }
  });

  it("marks model-drafted catalogs for native review", () => {
    expect(needsReview("en")).toBe(false);
    expect(needsReview("ps")).toBe(true);
  });
});

describe("translate", () => {
  it("fills values and uses the language's plural rules and digits", () => {
    expect(translate("en", "chat.title", { shop: "Saffron" })).toBe("Try it: chat with Saffron");
    expect(translate("en", "wake.seconds", { s: 1 })).toBe("about 1 second");
    expect(translate("en", "wake.seconds", { s: 9 })).toBe("about 9 seconds");
    expect(translate("ur-Latn", "chat.title", { shop: "Saffron" })).toContain("Saffron");
  });

  it("falls back along the chain until a catalog is loaded", async () => {
    expect(isLoaded("ps")).toBe(false);
    expect(translate("ps", "chat.send")).toBe("بھیجیں"); // Urdu, while Pashto loads
    await loadCatalog("ps");
    expect(translate("ps", "chat.send")).toBe("ولېږئ");
    expect(translate("hi-Latn", "chat.send")).toBe("Send");
  });

  it("shows a message rather than break when a value is missing", () => {
    expect(translate("en", "chat.title")).toBe("Try it: chat with {shop}");
  });
});

describe("languages", () => {
  it("resolves tags like the API", () => {
    expect(resolve("UR-latn")?.tag).toBe("ur-Latn");
    expect(resolve("ar-EG")?.tag).toBe("arz");
    expect(resolve("en-US")?.tag).toBe("en");
    expect(resolve("xx")).toBeUndefined();
    expect(chain("pa-Arab").map((l) => l.tag)).toEqual(["pa-Arab", "ur", "en"]);
    expect(languageOf("klingon").tag).toBe("en");
  });

  it("knows direction, fonts, digits and status", () => {
    for (const tag of ["ur", "pa-Arab", "ps", "sd", "ar"]) expect(dirOf(tag)).toBe("rtl");
    for (const tag of ["ur-Latn", "en", "hi", "bn"]) expect(dirOf(tag)).toBe("ltr");
    expect(fontOf("ur").family).toBe("Noto Nastaliq Urdu");
    expect(fontOf("ps").family).toBe("Noto Sans Arabic");
    expect(fontOf("hi").family).toBe("Noto Sans Devanagari");
    expect(localeOf(languageOf("bn"))).toBe("bn-u-nu-beng");
    expect(statusOf("ur")).toBe("beta");
    expect(statusOf("ta")).toBe("stretch");
  });

  it("loads a font stylesheet once, and never the preloaded ones", () => {
    const { doc, links } = fakeDocument();
    ensureFont("hi", doc);
    ensureFont("hi", doc);
    ensureFont("ur", doc);
    expect(links.length).toBe(1);
    expect(links[0]?.href).toContain("Noto+Sans+Devanagari");
  });

  it("picks shop text along the chain", () => {
    expect(pick({ en: "Lahore", ur: "لاہور" }, "ur")).toBe("لاہور");
    expect(pick({ en: "Lahore", ur: "لاہور" }, "ps")).toBe("لاہور");
    expect(pick({ en: "Lahore" }, "ur-Latn")).toBe("Lahore");
    expect(pick(undefined, "en")).toBe("");
  });

  it("tags names by script", () => {
    expect(scriptLang("نور سیلون")).toBe("ur");
    expect(scriptLang("नूर")).toBe("hi");
    expect(scriptLang("নূর")).toBe("bn");
    expect(scriptLang("Noor")).toBe("en");
  });

  it("starts in the saved language, else the browser's, else English", () => {
    expect(initialLang("ur-Latn", ["en-US"])).toBe("ur-Latn");
    expect(initialLang("klingon", ["ur-PK"])).toBe("ur");
    expect(initialLang(null, ["de-DE", "ar-EG"])).toBe("ar");
    expect(initialLang(null, ["pa-PK"])).toBe("pa-Arab");
    expect(initialLang(null, ["ta-IN"])).toBe("en");
    expect(initialLang(null, ["en-GB"])).toBe("en");
  });
});
