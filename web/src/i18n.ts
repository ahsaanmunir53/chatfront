// The web app reads the same catalog as the API: i18n/{en,ur,ur-Latn}.json at the repo root.
import en from "@i18n/en.json";
import ur from "@i18n/ur.json";
import roman from "@i18n/ur-Latn.json";

export type Lang = "en" | "ur" | "ur-Latn";
export type Key = keyof typeof en;
export type Text3 = Partial<Record<Lang, string>>;

export const LANGS: readonly Lang[] = ["ur-Latn", "ur", "en"];

const catalogs: Record<Lang, Record<string, string>> = { en, ur, "ur-Latn": roman };

export function translate(lang: Lang, key: Key, values?: Record<string, string | number>): string {
  const template = catalogs[lang][key] ?? en[key];
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    values && name in values ? String(values[name]) : whole,
  );
}

export function dirOf(lang: Lang): "rtl" | "ltr" {
  return lang === "ur" ? "rtl" : "ltr";
}

/** The best text for a language from a {en, ur, ur-Latn} object, falling back to English. */
export function pick(text: Text3 | undefined, lang: Lang): string {
  if (!text) return "";
  return text[lang] ?? text.en ?? "";
}

const ARABIC_SCRIPT = /[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]/;

/** The language to tag a shop-supplied name with, so it gets the right font and direction. */
export function scriptLang(text: string): "ur" | "en" {
  return ARABIC_SCRIPT.test(text) ? "ur" : "en";
}

export function initialLang(stored: string | null, browser: string): Lang {
  if (stored && (LANGS as readonly string[]).includes(stored)) return stored as Lang;
  return browser.toLowerCase().startsWith("ur") ? "ur" : "en";
}
