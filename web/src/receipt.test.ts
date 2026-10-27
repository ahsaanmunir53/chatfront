import { describe, expect, it } from "vitest";
import { canonical, verify } from "./receipt";

const toBase64Url = (bytes: ArrayBuffer) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

describe("receipts", () => {
  it("writes the canonical text exactly as the server signs it", () => {
    // The same payload and text are in api/tests/unit/test_payments.py.
    const payload = {
      shop: "گلمہر",
      order: "AB12CD",
      items: [{ quantity: 2, name: "Lawn" }],
      amount: 249700,
    };
    expect(canonical(payload)).toBe(
      '{"amount":249700,"items":[{"name":"Lawn","quantity":2}],"order":"AB12CD","shop":"گلمہر"}',
    );
  });

  it("accepts the shop's signature and nothing else", async () => {
    const pair = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, [
      "sign",
      "verify",
    ])) as CryptoKeyPair;
    const payload = { order: "AB12CD", amount: 249700 };
    const signature = toBase64Url(
      await crypto.subtle.sign(
        "Ed25519",
        pair.privateKey,
        new TextEncoder().encode(canonical(payload)),
      ),
    );
    const publicKey = toBase64Url(await crypto.subtle.exportKey("raw", pair.publicKey));
    expect(await verify(payload, signature, publicKey)).toBe("valid");
    expect(await verify({ ...payload, amount: 1 }, signature, publicKey)).toBe("invalid");
    expect(await verify(payload, signature, null)).toBe("invalid");
    expect(await verify(payload, "AAAA", publicKey)).toBe("invalid");
  });
});
