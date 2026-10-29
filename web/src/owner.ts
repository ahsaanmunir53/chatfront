// The owner's signed session in this browser. It arrives in the link Chatfront sends when a
// shop is made (…/owner#t=<token>), or from "Try as the owner" on a demo shop. The fragment
// never reaches a server log; the page moves it to storage and takes it out of the address.
const KEY = "chatfront.owner";

export function readOwnerToken(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function saveOwnerToken(token: string): void {
  try {
    sessionStorage.setItem(KEY, token);
  } catch {
    // Private windows can refuse storage; the console then lasts for this page only.
  }
}

export function clearOwnerToken(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // Nothing stored, nothing to clear.
  }
}

/** The token in a '#t=…' fragment, if there is one. */
export function tokenFromHash(hash: string): string | null {
  const token = new URLSearchParams(hash.replace(/^#/, "")).get("t");
  return token && /^[\w.-]{20,600}$/.test(token) ? token : null;
}
