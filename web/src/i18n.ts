// The web app reads the same files as the API: i18n/languages.json (the registry) and one ICU
// MessageFormat catalog per interface language. English, Urdu and Roman Urdu are bundled so the
// first paint never waits; the other catalogs load when someone picks them.
import { IntlMessageFormat } from "intl-messageformat";
import evalResults from "@i18n/eval-results.json";
import registry from "@i18n/languages.json";
import pakistan from "@i18n/countries/PK.json";
import review from "@i18n/review.json";
import en from "@i18n/en.json";
import ur from "@i18n/ur.json";
import roman from "@i18n/ur-Latn.json";

export type Key = keyof typeof en;
export type Lang = string; // a registry tag: "ur", "ur-Latn", "pa-Arab", "afb"
export type Text3 = Partial<Record<string, string>>;
export type Status = "supported" | "beta" | "stretch";
export type Values = Record<string, string | number>;
type Catalog = Record<string, string>;

export type Language = {
  tag: string;
  name: string;
  native: string;
  script: string;
  dir: string;
  tier: number;
  ui: boolean;
  cldr: string;
  digits: string;
  font: string;
  fallback: string[];
};

const LANGUAGES: Language[] = registry.languages;
const BY_TAG = new Map(LANGUAGES.map((language) => [language.tag, language]));
const BY_LOWER = new Map(LANGUAGES.map((language) => [language.tag.toLowerCase(), language]));
const ALIASES = new Map(
  Object.entries(registry.aliases as Record<string, string>).map(([from, to]) => [
    from.toLowerCase(),
    to,
  ]),
);
const SOURCE = registry.source;
const FONTS = registry.fonts as Record<string, string>;
const PRELOADED_FONTS = new Set(["Anek Latin", "Noto Nastaliq Urdu"]); // in index.html

const catalogs: Record<string, Catalog> = { en, ur, "ur-Latn": roman };
// Every other catalog, as its own chunk. The registry's files are not catalogs.
const lazyCatalogs = import.meta.glob<{ default: Catalog }>([
  "@i18n/*.json",
  "!@i18n/{en,ur,ur-Latn}.json",
  "!@i18n/{languages,review,eval-results,icu-cases,detect,numbers}.json",
]);

/** The language for any tag: case-insensitive, through aliases ("ar-EG" is Egyptian), then
 * dropping subtags ("en-US" is English). Undefined when Chatfront does not know it. */
export function resolve(tag: string | null | undefined): Language | undefined {
  if (!tag) return undefined;
  const parts = tag.replace(/_/g, "-").split("-");
  while (parts.length > 0) {
    const candidate = parts.join("-").toLowerCase();
    const found = BY_LOWER.get(candidate) ?? BY_TAG.get(ALIASES.get(candidate) ?? "");
    if (found) return found;
    parts.pop();
  }
  return undefined;
}

export function languageOf(tag: Lang): Language {
  const language = resolve(tag) ?? BY_TAG.get(SOURCE);
  if (!language) throw new Error("the registry has no source language");
  return language;
}

/** The language, its fallbacks, then English. */
export function chain(tag: Lang): Language[] {
  const start = languageOf(tag);
  const tags = [...new Set([start.tag, ...start.fallback, SOURCE])];
  return tags.map((t) => languageOf(t));
}

/** Interface languages, Pakistan's first (the order its country pack lists them). */
export const UI_LANGS: readonly Lang[] = (() => {
  const ui = LANGUAGES.filter((language) => language.ui).map((language) => language.tag);
  const local = pakistan.languages.filter((tag) => ui.includes(tag));
  return [...local, ...ui.filter((tag) => !local.includes(tag))];
})();

export function isLoaded(tag: Lang): boolean {
  return tag in catalogs;
}

/** Fetch an interface catalog that is not bundled. Resolves at once when it is. */
export async function loadCatalog(tag: Lang): Promise<void> {
  if (isLoaded(tag) || !languageOf(tag).ui) return;
  const entry = Object.entries(lazyCatalogs).find(([path]) => path.endsWith(`/${tag}.json`));
  if (!entry) return;
  catalogs[tag] = (await entry[1]()).default;
}

/** The ICU locale for a language: its CLDR locale with the digits its readers use. */
export function localeOf(language: Language): string {
  return `${language.cldr.replace(/_/g, "-")}-u-nu-${language.digits}`;
}

const formats = new Map<string, IntlMessageFormat>();

function format(template: string, language: Language, values?: Values): string {
  const cacheKey = `${language.tag}\u0000${template}`;
  let message = formats.get(cacheKey);
  if (!message) {
    message = new IntlMessageFormat(template, localeOf(language));
    formats.set(cacheKey, message);
  }
  try {
    return String(message.format(values));
  } catch {
    return template; // a missing value: show the message rather than break the page
  }
}

/** The message in the reader's language, or the nearest loaded language in its chain. */
export function translate(tag: Lang, key: Key, values?: Values): string {
  for (const language of chain(tag)) {
    const template = catalogs[language.tag]?.[key];
    if (template !== undefined) return format(template, language, values);
  }
  return key;
}

/** Format a message that is not in the catalog (shared ICU test cases use this). */
export function formatMessage(template: string, tag: Lang, values?: Values): string {
  return format(template, languageOf(tag), values);
}

export function dirOf(tag: Lang): "rtl" | "ltr" {
  return languageOf(tag).dir === "rtl" ? "rtl" : "ltr";
}

/** The font key ("nastaliq", "arabic", "devanagari"...) and family for a language. */
export function fontOf(tag: Lang): { key: string; family: string } {
  const key = languageOf(tag).font;
  return { key, family: FONTS[key] ?? FONTS.latin ?? "Anek Latin" };
}

/** Add the Google Fonts stylesheet for a script the first time it is needed. */
export function ensureFont(tag: Lang, doc: Document = document): void {
  const { key, family } = fontOf(tag);
  if (PRELOADED_FONTS.has(family) || doc.querySelector(`link[data-font="${key}"]`)) return;
  const link = doc.createElement("link");
  link.rel = "stylesheet";
  link.dataset.font = key;
  link.href = `https://fonts.googleapis.com/css2?family=${family.replace(/ /g, "+")}:wght@400;500;600;700&display=swap`;
  doc.head.append(link);
}

/** The best text from a {en, ur, ur-Latn, ...} object, along the language's chain. */
export function pick(text: Text3 | undefined, tag: Lang): string {
  if (!text) return "";
  for (const language of chain(tag)) {
    const found = text[language.tag];
    if (found) return found;
  }
  return "";
}

const SCRIPT_TAGS: [RegExp, Lang][] = [
  [/[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]/, "ur"],
  [/[ऀ-ॿ]/, "hi"],
  [/[ঀ-৿]/, "bn"],
];

/** The language to tag a shop-supplied name with, so it gets the right font and direction. */
export function scriptLang(text: string): Lang {
  return SCRIPT_TAGS.find(([pattern]) => pattern.test(text))?.[1] ?? "en";
}

/** The saved choice, else the first browser language with an interface, else English. */
export function initialLang(stored: string | null, browser: readonly string[]): Lang {
  const saved = resolve(stored);
  if (saved?.ui && saved.tag === stored) return saved.tag;
  for (const tag of browser) {
    const found = resolve(tag);
    if (!found) continue;
    const ui = chain(found.tag).find((language) => language.ui);
    if (ui) return ui.tag;
  }
  return SOURCE;
}

type Runs = Record<string, { live?: { pass_rate: number } }>;

/** "supported" once a live evaluation passed; "beta" until then; "stretch" for tier 2. */
export function statusOf(tag: Lang): Status {
  const language = languageOf(tag);
  if (language.tier > 1) return "stretch";
  const live = (evalResults.runs as Runs)[language.tag]?.live;
  return live && live.pass_rate >= registry.pass_mark ? "supported" : "beta";
}

/** Whether a person has checked this catalog, or a model drafted it. */
export function needsReview(tag: Lang): boolean {
  const catalogStatus = (review.catalogs as Record<string, { status: string }>)[tag]?.status;
  return catalogStatus === "needs-native-review";
}

/** A name, code or number set apart from the text around it, so right-to-left text and a
 * code such as "78MMG3" do not reorder each other. */
export function isolate(text: string): string {
  return `\u2068${text}\u2069`;
}
