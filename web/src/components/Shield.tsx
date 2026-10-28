// The shield in the console: decisions waiting for the owner (feature 104), and what the
// assistant may do on its own, ask first, or never do (feature 104). The customers list, with
// the verified-buyer badge (feature 38), is in Customers.tsx.
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type { Approval, ShopSettings } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";
import { useServer } from "../server";

const POLL_MS = 5000;

/** Polls the decisions this person may answer; refresh() after an answer. */
export function useApprovals(token: string) {
  const load = useCallback(() => api.approvals(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  return { list: data ?? [], refresh };
}

export function Approvals({
  token,
  list,
  onChange,
}: {
  token: string;
  list: Approval[];
  onChange: () => void;
}) {
  const { t } = useLang();
  const [busy, setBusy] = useState<string | null>(null);
  if (list.length === 0) return null;

  async function answer(approval: Approval, approve: boolean) {
    setBusy(approval.id);
    await api.answerApproval(token, approval.id, approve).catch(() => undefined);
    setBusy(null);
    onChange();
  }

  return (
    <section className="waiting approvals" aria-labelledby="approvals-title">
      <h2 id="approvals-title" className="waiting-title">
        {t("console.approvals_title")}
        <span className="console-count">{list.length}</span>
      </h2>
      <ul className="waiting-list">
        {list.map((approval) => (
          <li key={approval.id} className="waiting-item">
            <p className="waiting-summary" dir="auto">
              {approval.text}
            </p>
            <div className="waiting-actions">
              <button
                type="button"
                className="action action-primary"
                disabled={busy === approval.id}
                onClick={() => void answer(approval, true)}
              >
                {t("payment.approve")}
              </button>
              <button
                type="button"
                className="action"
                disabled={busy === approval.id}
                onClick={() => void answer(approval, false)}
              >
                {t("payment.reject")}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function SettingsPanel({ token }: { token: string }) {
  const { t } = useLang();
  const { state } = useServer();
  const [settings, setSettings] = useState<ShopSettings | null>(null);
  const [draft, setDraft] = useState<ShopSettings | null>(null);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "failed">("idle");

  useEffect(() => {
    if (state !== "online") return;
    api.settings(token).then(
      (loaded) => {
        setSettings(loaded);
        setDraft(loaded);
      },
      () => {
        // The panel stays hidden until the settings load.
      },
    );
  }, [token, state]);

  if (!settings || !draft) return null;

  function choose(action: string, level: string) {
    setDraft((current) =>
      current ? { ...current, autonomy: { ...current.autonomy, [action]: level } } : current,
    );
    setStatus("idle");
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!draft) return;
    setStatus("saving");
    try {
      const saved = await api.saveSettings(token, {
        autonomy: draft.autonomy,
        verified_after: draft.verified_after,
        low_stock_limit: draft.low_stock_limit,
      });
      setSettings(saved);
      setDraft(saved);
      setStatus("saved");
    } catch {
      setStatus("failed");
    }
  }

  return (
    <section className="settings-panel" aria-labelledby="settings-title">
      <h2 id="settings-title" className="section-title">
        {t("console.settings_title")}
      </h2>
      <p className="aside">{t("console.settings_lede")}</p>
      <form className="pay-block settings" onSubmit={(event) => void save(event)}>
        {Object.entries(settings.choices).map(([action, levels]) => (
          <fieldset key={action} className="setting">
            <legend>{t(`console.action.${action}` as Key)}</legend>
            {action === "discounts" && (
              <p className="pay-note">
                {t("console.action_note_discounts", { percent: settings.ask_discount_over })}
              </p>
            )}
            <div className="khata-kinds" role="radiogroup">
              {levels.map((level) => (
                <button
                  key={level}
                  type="button"
                  role="radio"
                  aria-checked={draft.autonomy[action] === level}
                  onClick={() => choose(action, level)}
                >
                  {t(`console.level.${level}` as Key)}
                </button>
              ))}
            </div>
          </fieldset>
        ))}
        <label className="setting-number">
          <span>{t("console.settings_verified_after")}</span>
          <input
            type="number"
            min={1}
            max={50}
            value={draft.verified_after}
            onChange={(e) => setDraft({ ...draft, verified_after: Number(e.target.value) || 1 })}
          />
        </label>
        <label className="setting-number">
          <span>{t("console.settings_low_stock")}</span>
          <input
            type="number"
            min={0}
            max={100000}
            value={draft.low_stock_limit}
            onChange={(e) => setDraft({ ...draft, low_stock_limit: Number(e.target.value) || 0 })}
          />
        </label>
        <div className="wording-actions">
          <button
            type="submit"
            className="button button-ink button-small"
            disabled={status === "saving"}
          >
            {t("console.save")}
          </button>
          {status === "saved" && <span role="status">{t("console.saved")}</span>}
          {status === "failed" && <span role="alert">{t("console.save_failed")}</span>}
        </div>
      </form>
    </section>
  );
}
