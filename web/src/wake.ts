// The server wake-up clock. Render's free tier sleeps after 15 idle minutes and takes about
// a minute to wake, so the page opens at once and counts down while the API wakes.

export type ServerState = "checking" | "waking" | "online" | "offline";

/** Render's usual cold start; the countdown starts here. */
export const WAKE_ESTIMATE_S = 55;
/** After this long without an answer, stop waiting and offer a retry. */
export const GIVE_UP_S = 180;
/** A first answer quicker than this means the server was awake: show nothing. */
export const QUIET_MS = 900;

export function secondsLeft(startedAt: number, now: number): number {
  return Math.max(0, Math.ceil(WAKE_ESTIMATE_S - (now - startedAt) / 1000));
}

/** 0 to 1, easing out so the bar never sits at full while still waiting. */
export function progress(startedAt: number, now: number): number {
  const elapsed = (now - startedAt) / 1000;
  return Math.min(0.97, 1 - Math.exp(-elapsed / (WAKE_ESTIMATE_S / 2.2)));
}

export function stateAfterMiss(startedAt: number, now: number): ServerState {
  return (now - startedAt) / 1000 >= GIVE_UP_S ? "offline" : "waking";
}
