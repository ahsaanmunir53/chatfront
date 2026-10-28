import { describe, expect, it } from "vitest";
import { validHours, validSpan } from "./hours";

describe("booking hours", () => {
  it("takes a span or an empty day", () => {
    expect(validSpan("11:00-20:00")).toBe(true);
    expect(validSpan(" 9:30 - 13:00 ")).toBe(true);
    expect(validSpan("00:00-24:00")).toBe(true);
    expect(validSpan("")).toBe(true);
  });

  it("refuses what the server would", () => {
    expect(validSpan("9 to 5")).toBe(false);
    expect(validSpan("20:00-11:00")).toBe(false);
    expect(validSpan("25:00-26:00")).toBe(false);
    expect(validHours({ mon: "11:00-20:00", tue: "11-20" })).toBe(false);
    expect(validHours({ mon: "11:00-20:00", sun: "" })).toBe(true);
  });
});
