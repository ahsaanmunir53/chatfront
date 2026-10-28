import { describe, expect, it } from "vitest";
import { keyBytes } from "./components/PhoneAlerts";

describe("the VAPID key for subscribing", () => {
  it("turns base64url into the 65-byte point push managers take", () => {
    const key =
      "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4";
    const bytes = keyBytes(key);
    expect(bytes.length).toBe(65);
    expect(bytes[0]).toBe(4); // uncompressed point
    expect(bytes[64]).toBe(0x0e);
  });
});
