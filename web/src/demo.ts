// The demo shops ship inside the app, read straight from the API's business templates,
// so their pages open before the server has woken up.
import type { Text3 } from "./i18n";

type TemplateFile = {
  id: string;
  name: Text3;
  demo: { slug: string; name: string; city: string; tagline: Text3 };
};

export type DemoShop = {
  slug: string;
  name: string;
  template: string;
  business_type: Text3;
  city: string;
  tagline: Text3;
};

// The order judges see them in: the payment-heavy shops first.
const ORDER = ["clothing", "electronics", "food", "grocery", "services", "wholesale"];

const files = import.meta.glob<TemplateFile>("@templates/*.json", {
  eager: true,
  import: "default",
});

export const DEMO_SHOPS: DemoShop[] = Object.values(files)
  .map((t) => ({
    slug: t.demo.slug,
    name: t.demo.name,
    template: t.id,
    business_type: t.name,
    city: t.demo.city,
    tagline: t.demo.tagline,
  }))
  .sort((a, b) => ORDER.indexOf(a.template) - ORDER.indexOf(b.template));

export function demoShop(slug: string): DemoShop | undefined {
  return DEMO_SHOPS.find((shop) => shop.slug === slug);
}
