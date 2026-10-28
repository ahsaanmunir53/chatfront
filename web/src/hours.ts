// Booking hours as the owner writes them (feature 18): "11:00-20:00" for a day, empty for a
// day the shop is closed. The server checks them again; this only catches a typo early.
export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const SPAN = /^\s*([01]?\d|2[0-3]):([0-5]\d)\s*-\s*([01]?\d|2[0-4]):([0-5]\d)\s*$/;

function minutes(hours = "0", mins = "0"): number {
  return Number(hours) * 60 + Number(mins);
}

/** One day's hours: empty (closed), or opening before closing. */
export function validSpan(span: string): boolean {
  if (span.trim() === "") return true;
  const found = SPAN.exec(span);
  return found !== null && minutes(found[1], found[2]) < minutes(found[3], found[4]);
}

export function validHours(days: Record<string, string>): boolean {
  return Object.values(days).every(validSpan);
}
