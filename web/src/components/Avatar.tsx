import { scriptLang } from "../i18n";

// Each business type gets its own colour, so shops are easy to tell apart at a glance.
const TEMPLATE_TONES: Record<string, string> = {
  clothing: "saffron",
  electronics: "sky",
  food: "chili",
  grocery: "leaf",
  services: "plum",
  wholesale: "teal",
};

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const letters = words.slice(0, 2).map((w) => Array.from(w)[0] ?? "");
  return letters.join("").toUpperCase() || "?";
}

export function Avatar({
  name,
  template,
  size = "md",
}: {
  name: string;
  template?: string;
  size?: "sm" | "md" | "lg";
}) {
  const tone = (template && TEMPLATE_TONES[template]) || "ink";
  return (
    <span
      className={`avatar avatar-${size} tone-${tone}`}
      lang={scriptLang(name)}
      aria-hidden="true"
    >
      {initials(name)}
    </span>
  );
}
