import { describe, expect, it } from "vitest";
import cases from "@i18n/money-cases.json";
import { formatMoney, packOf, toDigits, toMinor } from "./money";

describe("money", () => {
  it("is written exactly as the API writes it", () => {
    expect(cases.cases.length).toBeGreaterThanOrEqual(100);
    const wrong = cases.cases
      .map((c) => ({ ...c, web: formatMoney(c.amount, c.country, c.lang) }))
      .filter((c) => c.web !== c.text);
    expect(wrong).toEqual([]);
  });

  it("falls back to Pakistan for an unknown country", () => {
    expect(packOf("xx").code).toBe("PK");
    expect(packOf(null).currency).toBe("PKR");
    expect(packOf("bd").code).toBe("BD");
  });

  it("knows minor units and digits", () => {
    expect(packOf("PK").minorDigits).toBe(2);
    expect(packOf("VN").minorDigits).toBe(0);
    expect(toDigits("4,850", "beng")).toBe("৪,৮৫০");
    expect(toDigits("4,850", "latn")).toBe("4,850");
    expect(toMinor(4850, "PK")).toBe(485_000);
  });
});
