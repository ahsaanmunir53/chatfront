// Checking a signed receipt in the browser (feature 26). The shop signs the canonical JSON of
// the receipt (keys sorted, no spaces, characters as they are) with its Ed25519 key; this page
// rebuilds that text itself and checks the signature with the shop's public key, so it does
// not have to trust the server's own answer.

export type ReceiptItem = { name: string; quantity: number };

export type ReceiptPayload = {
  v: number;
  receipt: string;
  shop: string;
  shop_slug: string;
  country: string;
  order: string;
  items: ReceiptItem[];
  currency: string;
  minor_digits: number;
  amount: number;
  order_total: number;
  paid_so_far: number;
  balance: number;
  method: "transfer" | "cod" | null;
  verified_by: "sms" | "owner";
  transaction_id: string | null;
  issued_at: string;
};

export type Verdict = "valid" | "invalid" | "unsupported";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

/** The same text Python's json.dumps(sort_keys=True, separators=(",", ":")) writes. */
export function canonical(value: unknown): string {
  return JSON.stringify(sorted(value as Json));
}

function sorted(value: Json): Json {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === "object") {
    const out: { [key: string]: Json } = {};
    for (const [key, item] of Object.entries(value).sort(([a], [b]) => (a < b ? -1 : 1))) {
      out[key] = sorted(item);
    }
    return out;
  }
  return value;
}

function fromBase64Url(text: string): Uint8Array<ArrayBuffer> {
  const base64 = text.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/** "unsupported" when this browser has no Ed25519; the page then shows the server's check. */
export async function verify(
  payload: unknown,
  signature: string,
  publicKey: string | null,
): Promise<Verdict> {
  if (!publicKey) return "invalid";
  let key: CryptoKey;
  try {
    key = await crypto.subtle.importKey(
      "raw",
      fromBase64Url(publicKey),
      { name: "Ed25519" },
      false,
      ["verify"],
    );
  } catch (error) {
    return error instanceof DOMException && error.name === "NotSupportedError"
      ? "unsupported"
      : "invalid";
  }
  try {
    const text = new TextEncoder().encode(canonical(payload));
    const ok = await crypto.subtle.verify("Ed25519", key, fromBase64Url(signature), text);
    return ok ? "valid" : "invalid";
  } catch {
    return "invalid";
  }
}
