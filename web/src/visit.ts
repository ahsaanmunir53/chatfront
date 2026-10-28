// What a shared link brought with it, kept for this visit to one shop: a friend's code
// (?ref=, feature 20) and the group order a friend is adding a part to (?group=, feature 22).
// Session storage is a convenience only: without it the code is typed by hand at checkout.
const CODE = /^[A-Z0-9]{3,12}$/;
const key = (kind: "ref" | "group", slug: string) => `chatfront.${kind}.${slug}`;

/** A code as the API takes it, or null for anything else. */
export function cleanCode(raw: string | null | undefined): string | null {
  const code = (raw ?? "").replace(/\s+/g, "").toUpperCase();
  return CODE.test(code) ? code : null;
}

function read(kind: "ref" | "group", slug: string): string | null {
  try {
    return cleanCode(sessionStorage.getItem(key(kind, slug)));
  } catch {
    return null;
  }
}

function write(kind: "ref" | "group", slug: string, code: string | null): void {
  try {
    if (code) sessionStorage.setItem(key(kind, slug), code);
    else sessionStorage.removeItem(key(kind, slug));
  } catch {
    // Without storage the link's code lasts as long as the page.
  }
}

export const readRef = (slug: string) => read("ref", slug);
export const readGroup = (slug: string) => read("group", slug);
export const forgetGroup = (slug: string) => write("group", slug, null);

/** Keeps the codes a shop link carries, for the checkout. */
export function rememberVisit(slug: string, params: URLSearchParams): void {
  const ref = cleanCode(params.get("ref"));
  const group = cleanCode(params.get("group"));
  if (ref) write("ref", slug, ref);
  if (group) write("group", slug, group);
}

/** The link that opens a group order's page. */
export function groupLink(origin: string, base: string, slug: string, code: string): string {
  return `${origin}${base.replace(/\/$/, "")}/s/${slug}/g/${code}`;
}
