// A busy day in a demo copy of a shop (feature 110): simulated customers chat with it at once,
// and the owner watches them arrive in the inbox and the orders, then sees how each chat ended.
import { useCallback, useState } from "react";
import { ApiError, api } from "../api";
import type { BusyDayRun } from "../api";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const SIZES = [10, 20, 50] as const;
const POLL_MS = 4000;

function Outcomes({ run }: { run: BusyDayRun }) {
  const { t } = useLang();
  const rows = [
    { key: "ordered", line: t("busy.ordered", { count: run.ordered }), share: run.ordered },
    { key: "handover", line: t("busy.handover", { count: run.handover }), share: run.handover },
    { key: "left", line: t("busy.left", { count: run.left }), share: run.left },
    { key: "stuck", line: t("busy.stuck", { count: run.stuck }), share: run.stuck },
  ].filter((row) => row.share > 0);
  return (
    <>
      <div className="busy-bar" aria-hidden="true">
        {rows.map((row) => (
          <span key={row.key} className={`busy-${row.key}`} style={{ flexGrow: row.share }} />
        ))}
        {run.active > 0 && <span className="busy-active" style={{ flexGrow: run.active }} />}
      </div>
      <ul className="busy-key">
        {rows.map((row) => (
          <li key={row.key}>
            <span className={`dash-swatch busy-${row.key}`} aria-hidden="true" />
            {row.line}
          </li>
        ))}
      </ul>
    </>
  );
}

export function BusyDayPanel({ token, canStart }: { token: string; canStart: boolean }) {
  const { t } = useLang();
  const load = useCallback(() => api.busyDay(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [size, setSize] = useState<(typeof SIZES)[number]>(20);
  const [problem, setProblem] = useState<string | null>(null);
  if (!data?.mode) return null;
  const run = data.run;
  const running = run !== null && run.finished_at === null;
  async function start() {
    try {
      await api.startBusyDay(token, size);
      setProblem(null);
      refresh();
    } catch (error) {
      setProblem(error instanceof ApiError && error.status === 429 ? "busy.full" : "busy.failed");
    }
  }
  return (
    <section className="busy-day" aria-labelledby="busy-title">
      <h2 id="busy-title" className="section-title">
        {t("busy.title")}
      </h2>
      <p className="lede">{t("busy.lede")}</p>
      <p className="pay-note">{t(data.mode === "live" ? "busy.live" : "busy.replay")}</p>
      {run && (
        <div className="busy-run" role="status">
          <p>
            {running
              ? t("busy.running", { count: run.active })
              : t("busy.done", { count: run.customers, messages: run.messages })}
          </p>
          <Outcomes run={run} />
        </div>
      )}
      {canStart && !running && (
        <div className="busy-start">
          <div className="segment-filter" role="group" aria-label={t("busy.how_many")}>
            {SIZES.map((count) => (
              <button
                key={count}
                type="button"
                className="chip"
                aria-pressed={size === count}
                onClick={() => setSize(count)}
              >
                {t("busy.customers", { count })}
              </button>
            ))}
          </div>
          <button type="button" className="button button-ink" onClick={() => void start()}>
            {t("busy.start")}
          </button>
        </div>
      )}
      {problem && <p role="alert">{t(problem === "busy.full" ? "busy.full" : "busy.failed")}</p>}
    </section>
  );
}
