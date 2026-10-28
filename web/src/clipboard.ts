// Copying a link or an address for the person to paste somewhere else.
import { useCallback, useState } from "react";

/** Copy text; false when the browser does not allow it (not a secure page, or refused). */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** Whether the last copy worked, so the button can say "Copied" only when it did. */
export function useCopy() {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(async (text: string) => setCopied(await copyText(text)), []);
  const reset = useCallback(() => setCopied(false), []);
  return { copied, copy, reset };
}
