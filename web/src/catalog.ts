// Products on the shop page. Demo shops' products ship inside the app (from the business
// templates, like the shops themselves), so the catalog shows before the server wakes; the
// live list from the API replaces them once it answers.
import type { PublicProduct } from "./api";
import { chain } from "./i18n";
import type { Lang } from "./i18n";
import { packOf } from "./money";

type DemoVariant = { options?: Record<string, string>; price?: number | null; stock?: number | null };
type DemoProduct = {
  name: string;
  price: number;
  category: string;
  description: string;
  variants?: DemoVariant[];
};
type TemplateFile = { id: string; demo: { slug: string; products?: DemoProduct[] } };

export type ShopProduct = {
  key: string;
  name: string;
  description: string;
  translations: Record<string, { name?: string; description?: string }>;
  price: number; // minor units, the lowest a variant sells for
  priceVaries: boolean;
  options: string[]; // one label per variant: "M", "Sea green"
  imageUrl: string | null;
  inStock: boolean;
  // What can go in the cart; empty until the live catalog answers.
  variants: { catalogId: string; label: string; inStock: boolean }[];
};

const files = import.meta.glob<TemplateFile>("@templates/*.json", {
  eager: true,
  import: "default",
});

function optionLabel(options: Record<string, string> | undefined): string {
  return Object.values(options ?? {}).join(" ");
}

function lowest(prices: number[], fallback: number): { price: number; varies: boolean } {
  if (prices.length === 0) return { price: fallback, varies: false };
  const price = Math.min(...prices);
  return { price, varies: prices.some((p) => p !== price) };
}

/** The demo shop's products as the API would list them, prices in the pack's minor units. */
export function bundledProducts(slug: string): ShopProduct[] | undefined {
  const template = Object.values(files).find((file) => file.demo.slug === slug);
  if (!template?.demo.products) return undefined;
  const scale = 10 ** packOf(null).minorDigits; // demo shops are in Pakistan
  return template.demo.products.map((product, index) => {
    const variants = product.variants ?? [{}];
    const { price, varies } = lowest(
      variants.map((v) => (v.price ?? product.price) * scale),
      product.price * scale,
    );
    return {
      key: `demo-${index}`,
      name: product.name,
      description: product.description,
      translations: {},
      price,
      priceVaries: varies,
      options: variants.map((v) => optionLabel(v.options)).filter(Boolean),
      imageUrl: null,
      inStock: variants.some((v) => v.stock == null || v.stock > 0),
      variants: [],
    };
  });
}

export function fromApi(product: PublicProduct): ShopProduct {
  const { price, varies } = lowest(
    product.variants.map((v) => v.price),
    product.price,
  );
  return {
    key: product.code,
    name: product.name,
    description: product.description,
    translations: product.translations,
    price,
    priceVaries: varies,
    options: product.variants.map((v) => optionLabel(v.options)).filter(Boolean),
    imageUrl: product.image_url,
    inStock: product.in_stock,
    variants: product.variants.map((v) => ({
      catalogId: v.catalog_id,
      label: optionLabel(v.options),
      inStock: v.in_stock,
    })),
  };
}

/** The product's name and description in the reader's language or a close one, else the
 * owner's own words. English is not a fallback here: the owner's original reads better. */
export function productText(
  product: Pick<ShopProduct, "name" | "description" | "translations">,
  tag: Lang,
): { name: string; description: string; lang: Lang | null } {
  const languages = chain(tag);
  for (const language of languages.length > 1 ? languages.slice(0, -1) : languages) {
    const text = product.translations[language.tag];
    if (text?.name) {
      return {
        name: text.name,
        description: text.description || product.description,
        lang: language.tag,
      };
    }
  }
  return { name: product.name, description: product.description, lang: null };
}
