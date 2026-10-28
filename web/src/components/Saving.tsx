// The status line under a settings form: a save in progress, saved, or failed.
import { useCallback, useState } from "react";
import { useLang } from "../lang";

export type Saving = "idle" | "saving" | "saved" | "failed";

export function Status({ state }: { state: Saving }) {
  const { t } = useLang();
  if (state === "saved") return <span role="status">{t("console.saved")}</span>;
  if (state === "failed") return <span role="alert">{t("console.offers_failed")}</span>;
  return null;
}

/** Runs a save and keeps its state for the form's status line. */
export function useSave(onDone: () => void) {
  const [state, setState] = useState<Saving>("idle");
  const run = useCallback(
    async (step: () => Promise<unknown>) => {
      setState("saving");
      try {
        await step();
        setState("saved");
        onDone();
        return true;
      } catch {
        setState("failed");
        return false;
      }
    },
    [onDone],
  );
  return { state, run };
}
