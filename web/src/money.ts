// Money on the page, written exactly as the API writes it (chatfront.core.countries
// .format_money): the country pack's grouping and symbol, the reader's own digits. Amounts are
// integer minor units, as the API sends them. i18n/money-cases.json keeps the two in step.
import { languageOf } from "./i18n";
import type { Lang } from "./i18n";

type Sign = { symbol: string; position: "before" | "after" };
type MoneyRules = Sign & {
  show_minor: "nonzero" | "always" | "never";
  local_symbols: Record<string, Sign>;
};

export type Pack = {
  code: string;
  currency: string;
  minorDigits: number;
  locale: string;
  money: MoneyRules;
};

// Only these fields of each pack reach the bundle; phones and wallets stay out.
const MONEY = import.meta.glob<MoneyRules>("@i18n/countries/*.json", {
  eager: true,
  import: "money",
});
const CURRENCY = import.meta.glob<string>("@i18n/countries/*.json", {
  eager: true,
  import: "currency",
});
const LOCALE = import.meta.glob<string>("@i18n/countries/*.json", {
  eager: true,
  import: "number_locale",
});
// From the pack, not Intl: browsers' ICU says 0 for the rupee, CLDR and the API say 2.
const MINOR = import.meta.glob<number>("@i18n/countries/*.json", {
  eager: true,
  import: "minor_digits",
});

const PACKS: Record<string, Pack> = Object.fromEntries(
  Object.entries(MONEY).map(([path, money]) => {
    const code = path.slice(path.lastIndexOf("/") + 1, -".json".length);
    const pack: Pack = {
      code,
      currency: CURRENCY[path] ?? "",
      minorDigits: MINOR[path] ?? 2,
      locale: LOCALE[path] ?? "en",
      money,
    };
    return [code, pack];
  }),
);

export const DEFAULT_COUNTRY = "PK";

/** The pack for a country code; Pakistan's when there is none, as on the API. */
export function packOf(country: string | null | undefined): Pack {
  const pack = PACKS[(country ?? "").toUpperCase()] ?? PACKS[DEFAULT_COUNTRY];
  if (!pack) throw new Error("no country pack for Pakistan");
  return pack;
}

const digitTables = new Map<string, string>();

/** The ten digits of a numbering system, from the browser's own CLDR data. */
function digitsOf(system: string): string {
  let table = digitTables.get(system);
  if (!table) {
    const format = new Intl.NumberFormat(`en-u-nu-${system}`, { useGrouping: false });
    table = Array.from({ length: 10 }, (_, digit) => format.format(digit)).join("");
    digitTables.set(system, table);
  }
  return table;
}

/** Latin digits in text written in another numbering system. */
export function toDigits(text: string, system: string): string {
  if (system === "latn") return text;
  const table = digitsOf(system);
  return text.replace(/[0-9]/g, (digit) => table[Number(digit)] ?? digit);
}

/** 'Rs 4,850', '4,850 روپے', '৳ ১২,৩৪,৫৬৭', 'Rs 4,850.50'. */
export function formatMoney(amount: number, country: string | null | undefined, tag: Lang): string {
  const pack = packOf(country);
  const language = languageOf(tag);
  const digits = pack.minorDigits;
  const scale = 10 ** digits;
  const whole = Math.abs(amount);
  const major = Math.floor(whole / scale);
  const minor = whole % scale;
  const locale = `${pack.locale}-u-nu-${language.digits}`;
  let number = toDigits(
    new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(major),
    language.digits,
  );
  const rules = pack.money;
  const show = rules.show_minor === "always" || (rules.show_minor === "nonzero" && minor > 0);
  if (show && digits > 0) {
    const parts = new Intl.NumberFormat(locale).formatToParts(1.5);
    const decimal = parts.find((part) => part.type === "decimal")?.value ?? ".";
    number += decimal + toDigits(String(minor).padStart(digits, "0"), language.digits);
  }
  const sign = amount < 0 ? "-" : "";
  const symbol = rules.local_symbols[language.tag] ?? rules;
  return symbol.position === "before"
    ? `${sign}${symbol.symbol} ${number}`
    : `${sign}${number} ${symbol.symbol}`;
}

/** Major units as typed by an owner (4850) to minor units (485000). */
export function toMinor(major: number, country: string | null | undefined): number {
  return Math.round(major * 10 ** packOf(country).minorDigits);
}

// Zero in each digit set an owner may type in: Arabic-Indic, Persian/Urdu, Devanagari, Bengali.
const ZEROS = [0x0660, 0x06f0, 0x0966, 0x09e6];

/** An amount as typed in a form ("4,850", "۴۸۵۰", "৪৮৫০.৫০") in major units, or null. */
export function parseAmount(typed: string): number | null {
  let ascii = "";
  for (const char of typed.trim()) {
    const code = char.codePointAt(0) ?? 0;
    const zero = ZEROS.find((start) => code >= start && code <= start + 9);
    ascii += zero === undefined ? char : String(code - zero);
  }
  const cleaned = ascii.replace(/[,\u066c\s]/g, "").replace("\u066b", ".");
  if (!/^\d+(\.\d+)?$/.test(cleaned)) return null;
  const amount = Number(cleaned);
  return amount > 0 ? amount : null;
}
