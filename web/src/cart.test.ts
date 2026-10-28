import { beforeEach, describe, expect, it } from "vitest";
import { parseProducts, readCart, sourceOf, writeCart } from "./cart";

describe("a Facebook or Instagram checkout link", () => {
  it("reads ids and quantities, adding repeats and skipping junk", () => {
    expect(parseProducts("K7Q2M-2:2,AB12C:1,K7Q2M-2:1")).toEqual([
      { item: "K7Q2M-2", quantity: 3 },
      { item: "AB12C", quantity: 1 },
    ]);
    expect(parseProducts("AB12C")).toEqual([{ item: "AB12C", quantity: 1 }]);
    expect(parseProducts("AB12C:0,:3,X:abc,Y:500")).toEqual([{ item: "Y", quantity: 99 }]);
    expect(parseProducts(null)).toEqual([]);
  });

  it("names where the buyer came from", () => {
    expect(sourceOf("instagram")).toBe("instagram");
    expect(sourceOf("facebook")).toBe("facebook");
    expect(sourceOf("somewhere")).toBe("shop_page");
    expect(sourceOf(null)).toBe("shop_page");
  });
});

class MemoryStorage {
  private items = new Map<string, string>();
  getItem(name: string): string | null {
    return this.items.get(name) ?? null;
  }
  setItem(name: string, value: string): void {
    this.items.set(name, value);
  }
  removeItem(name: string): void {
    this.items.delete(name);
  }
  clear(): void {
    this.items.clear();
  }
}

describe("the cart in this browser", () => {
  beforeEach(() => {
    Object.defineProperty(globalThis, "localStorage", {
      value: new MemoryStorage(),
      configurable: true,
    });
  });

  it("keeps lines per shop and drops what does not fit", () => {
    writeCart("a", [{ item: "X-1", quantity: 2 }]);
    expect(readCart("a")).toEqual([{ item: "X-1", quantity: 2 }]);
    expect(readCart("b")).toEqual([]);
    localStorage.setItem("chatfront.cart.a", '[{"item":"X","quantity":400},{"bad":1}]');
    expect(readCart("a")).toEqual([{ item: "X", quantity: 99 }]);
    localStorage.setItem("chatfront.cart.a", "not json");
    expect(readCart("a")).toEqual([]);
    writeCart("a", []);
    expect(localStorage.getItem("chatfront.cart.a")).toBeNull();
  });
});
