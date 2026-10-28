import { describe, expect, it } from "vitest";
import type { PublicProduct } from "./api";
import { bundledProducts, fromApi, productText } from "./catalog";
import { formatMoney } from "./money";
import { tokenFromHash } from "./owner";

describe("catalog", () => {
  it("ships every demo shop's products, priced in paisa like the API", () => {
    const lawn = bundledProducts("gulmohar-lawn");
    expect(lawn?.length).toBe(4);
    const first = lawn?.[0];
    expect(first?.name).toBe("Printed lawn 3-piece");
    expect(first?.price).toBe(485_000);
    expect(first?.options).toEqual(["Sea green", "Rust"]);
    expect(first && formatMoney(first.price, "PK", "ur")).toBe("4,850 روپے");
    expect(bundledProducts("no-such-shop")).toBeUndefined();
  });

  it("reads the live list: the lowest variant price, and whether prices vary", () => {
    const product: PublicProduct = {
      code: "KRT01",
      name: "Linen kurta",
      description: "Plain linen kurta.",
      category: "kurtas",
      price: 275_000,
      attributes: {},
      translations: { ur: { name: "لینن کرتا", description: "" } },
      image_url: null,
      in_stock: false,
      ships_from: null,
      size_chart: {
        name: "Kurtas",
        unit: "in",
        measures: ["chest"],
        rows: [{ size: "M", ranges: { chest: [36, 37] } }],
      },
      variants: [
        { id: "a", catalog_id: "KRT01-1", options: { size: "M" }, price: 275_000, in_stock: false },
        {
          id: "b",
          catalog_id: "KRT01-2",
          options: { size: "XL" },
          price: 299_000,
          in_stock: false,
        },
      ],
    };
    const shown = fromApi(product);
    expect(shown).toMatchObject({
      key: "KRT01",
      price: 275_000,
      priceVaries: true,
      inStock: false,
    });
    expect(shown.options).toEqual(["M", "XL"]);
    expect(shown.variants).toEqual([
      { catalogId: "KRT01-1", label: "M", inStock: false },
      { catalogId: "KRT01-2", label: "XL", inStock: false },
    ]);
    expect(productText(shown, "ur")).toEqual({
      name: "لینن کرتا",
      description: "Plain linen kurta.",
      lang: "ur",
    });
    // Punjabi falls back to Urdu; Bengali has no close language here, so the owner's words.
    expect(productText(shown, "pa-Arab").name).toBe("لینن کرتا");
    expect(productText(shown, "bn")).toMatchObject({ name: "Linen kurta", lang: null });
  });
});

describe("owner sign-in link", () => {
  it("takes the session from the fragment and nothing else", () => {
    const token = "eyJzIjoiMSJ9.c2lnbmF0dXJlLWJ5dGVz";
    expect(tokenFromHash(`#t=${token}`)).toBe(token);
    expect(tokenFromHash("")).toBeNull();
    expect(tokenFromHash("#t=short")).toBeNull();
    expect(tokenFromHash("#t=<script>alert(1)</script>xxxxxxxxxxxx")).toBeNull();
  });
});
