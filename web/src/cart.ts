// The shop page's cart (feature 80): lines by catalog id, kept in this browser per shop as a
// convenience. Prices are never kept here; the API quotes them when the buyer checks out.
import { useCallback, useEffect, useState } from "react";

export type CartLine = { item: string; quantity: number };

export const MAX_QUANTITY = 99;
const MAX_LINES = 30;
const key = (slug: string) => `chatfront.cart.${slug}`;
const listeners = new Set<() => void>();

function clamp(quantity: number): number {
  return Math.min(MAX_QUANTITY, Math.max(1, Math.round(quantity)));
}

export function readCart(slug: string): CartLine[] {
  try {
    const raw = JSON.parse(localStorage.getItem(key(slug)) ?? "[]") as unknown;
    if (!Array.isArray(raw)) return [];
    return raw
      .filter(
        (line): line is CartLine =>
          typeof line?.item === "string" && typeof line?.quantity === "number",
      )
      .slice(0, MAX_LINES)
      .map((line) => ({ item: line.item, quantity: clamp(line.quantity) }));
  } catch {
    return [];
  }
}

export function writeCart(slug: string, lines: CartLine[]): void {
  try {
    if (lines.length === 0) localStorage.removeItem(key(slug));
    else localStorage.setItem(key(slug), JSON.stringify(lines.slice(0, MAX_LINES)));
  } catch {
    // Without storage the cart lives as long as the page.
  }
  listeners.forEach((listener) => listener());
}

/** Lines from a Facebook or Instagram shop link: products=<id>:<qty>,<id>:<qty> (already
 * decoded by URLSearchParams). Repeated ids add up; a missing quantity is one. */
export function parseProducts(param: string | null): CartLine[] {
  const lines = new Map<string, number>();
  for (const part of (param ?? "").split(",")) {
    const cut = part.lastIndexOf(":");
    const item = (cut >= 0 ? part.slice(0, cut) : part).trim();
    const quantity = cut >= 0 ? Number.parseInt(part.slice(cut + 1), 10) : 1;
    if (!item || item.length > 40 || !Number.isFinite(quantity) || quantity < 1) continue;
    lines.set(item, clamp((lines.get(item) ?? 0) + quantity));
  }
  return [...lines].slice(0, MAX_LINES).map(([item, quantity]) => ({ item, quantity }));
}

/** Where a checkout link came from, as the API names it. */
const ORIGINS = ["facebook", "instagram", "assistant"] as const;
type Origin = (typeof ORIGINS)[number];

export function sourceOf(cartOrigin: string | null): "shop_page" | Origin {
  return ORIGINS.find((origin) => origin === cartOrigin) ?? "shop_page";
}

export function useCart(slug: string) {
  const [lines, setLines] = useState<CartLine[]>(() => readCart(slug));
  useEffect(() => {
    const update = () => setLines(readCart(slug));
    update();
    listeners.add(update);
    window.addEventListener("storage", update);
    return () => {
      listeners.delete(update);
      window.removeEventListener("storage", update);
    };
  }, [slug]);
  const set = useCallback(
    (item: string, quantity: number) => {
      const others = readCart(slug).filter((line) => line.item !== item);
      writeCart(slug, quantity > 0 ? [...others, { item, quantity: clamp(quantity) }] : others);
    },
    [slug],
  );
  const add = useCallback(
    (item: string) => {
      const have = readCart(slug).find((line) => line.item === item)?.quantity ?? 0;
      set(item, have + 1);
    },
    [slug, set],
  );
  const replace = useCallback((next: CartLine[]) => writeCart(slug, next), [slug]);
  const count = lines.reduce((sum, line) => sum + line.quantity, 0);
  return { lines, count, add, set, replace };
}
