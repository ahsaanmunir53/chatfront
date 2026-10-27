import { useEffect, useState } from "react";
import { useLang } from "../lang";
import { useServer } from "../server";
import { progress, secondsLeft } from "../wake";
import { CheckIcon } from "./Icons";

/** A pill at the bottom of the screen while the API wakes; it says "Online" and goes away. */
export function WakeBar({ placement = "float" }: { placement?: "float" | "inline" }) {
  const { t } = useLang();
  const { state, startedAt, retry } = useServer();
  const [now, setNow] = useState(() => Date.now());
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (state === "waking") setVisible(true);
    if (state !== "online" || !visible) return;
    const hide = window.setTimeout(() => setVisible(false), 1800);
    return () => window.clearTimeout(hide);
  }, [state, visible]);

  useEffect(() => {
    if (state !== "waking") return;
    const tick = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(tick);
  }, [state]);

  if (!visible && state !== "offline") return null;

  const left = secondsLeft(startedAt, now);
  return (
    <div className={`wake wake-${placement} wake-${state}`} role="status" aria-live="polite">
      {state === "online" ? (
        <span className="wake-row">
          <CheckIcon size={18} />
          {t("wake.online")}
        </span>
      ) : state === "offline" ? (
        <span className="wake-row">
          {t("wake.offline")}
          <button type="button" className="wake-retry" onClick={retry}>
            {t("wake.retry")}
          </button>
        </span>
      ) : (
        <>
          <span className="wake-row">
            <span className="wake-spinner" aria-hidden="true" />
            <span>{t("wake.waking")}</span>
            <span className="wake-count">
              {left > 0 ? t("wake.seconds", { s: left }) : t("wake.slow")}
            </span>
          </span>
          <span className="wake-track" aria-hidden="true">
            <span
              className="wake-fill"
              style={{ transform: `scaleX(${progress(startedAt, now)})` }}
            />
          </span>
          <span className="wake-note">{t("wake.note")}</span>
        </>
      )}
    </div>
  );
}
