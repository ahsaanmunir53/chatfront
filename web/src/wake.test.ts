import { describe, expect, it } from "vitest";
import { GIVE_UP_S, WAKE_ESTIMATE_S, progress, secondsLeft, stateAfterMiss } from "./wake";

describe("wake-up clock", () => {
  it("counts down from the estimate and stops at zero", () => {
    expect(secondsLeft(0, 0)).toBe(WAKE_ESTIMATE_S);
    expect(secondsLeft(0, 10_500)).toBe(WAKE_ESTIMATE_S - 10);
    expect(secondsLeft(0, 600_000)).toBe(0);
  });

  it("fills the bar without ever reaching the end on its own", () => {
    expect(progress(0, 0)).toBe(0);
    expect(progress(0, 20_000)).toBeGreaterThan(0.3);
    expect(progress(0, 10_000_000)).toBe(0.97);
  });

  it("gives up after a few minutes", () => {
    expect(stateAfterMiss(0, 30_000)).toBe("waking");
    expect(stateAfterMiss(0, GIVE_UP_S * 1000)).toBe("offline");
  });
});
