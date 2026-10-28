// The consent log (feature 108): who said yes or no to offers, and the shop's own yes or no to
// sharing fraud reports with other shops and its sales history with a lender. Every answer is a
// new row with the words it answered; nothing is overwritten.
import { useCallback, useState } from "react";
import { api } from "../api";
import type { Consents } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 30_000;
const SHOP_PURPOSES = ["fraud_list", "credit_passport"] as const;
type ShopPurpose = (typeof SHOP_PURPOSES)[number];

function ShopSwitch({
  token,
  purpose,
  granted,
  canChange,
  onSaved,
}: {
  token: string;
  purpose: ShopPurpose;
  granted: boolean | null;
  canChange: boolean;
  onSaved: (fresh: Consents) => void;
}) {
  const { t } = useLang();
  const [failed, setFailed] = useState(false);
  async function answer(yes: boolean) {
    try {
      onSaved(await api.shopConsent(token, purpose, yes));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }
  return (
    <div className="consent-switch">
      <p>
        <strong>{t(`console.consent_${purpose}` as Key)}</strong>{" "}
        <span className={granted ? "pay-on" : "pay-off"}>
          {granted === null
            ? t("console.consent_not_asked")
            : granted
              ? t("console.consent_yes")
              : t("console.consent_no")}
        </span>
      </p>
      <p className="aside" dir="auto">
        {t(`consent.${purpose}_terms` as Key)}
      </p>
      {canChange && (
        <div className="wording-actions">
          <button
            type="button"
            className="action"
            disabled={granted === true}
            onClick={() => void answer(true)}
          >
            {t("console.consent_agree")}
          </button>
          <button
            type="button"
            className="action"
            disabled={granted === false}
            onClick={() => void answer(false)}
          >
            {t("console.consent_refuse")}
          </button>
        </div>
      )}
      {failed && <p role="alert">{t("console.offers_failed")}</p>}
    </div>
  );
}

export function ConsentsPanel({ token, canChange }: { token: string; canChange: boolean }) {
  const { t, lang } = useLang();
  const load = useCallback(() => api.consents(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [fresh, setFresh] = useState<Consents | null>(null);
  const shown = fresh ?? data;
  if (!shown) return null;
  function saved(next: Consents) {
    setFresh(next);
    refresh();
  }
  return (
    <section className="pay-panel consents" aria-labelledby="consents-title">
      <h2 id="consents-title" className="section-title">
        {t("console.consents_title")}
      </h2>
      <p className="aside">
        {t("console.consents_offers", { yes: shown.offers_yes, no: shown.offers_no })}
      </p>
      <div className="pay-columns">
        <div className="pay-block">
          {SHOP_PURPOSES.map((purpose) => (
            <ShopSwitch
              key={purpose}
              token={token}
              purpose={purpose}
              granted={shown[purpose]}
              canChange={canChange}
              onSaved={saved}
            />
          ))}
        </div>
        <div className="pay-block">
          <h3 id="consent-log-title" className="pay-block-title">
            {t("console.consents_log")}
          </h3>
          {shown.rows.length === 0 ? (
            <p className="aside">{t("console.consents_none")}</p>
          ) : (
            <ol className="pay-rows" aria-labelledby="consent-log-title">
              {shown.rows.map((row, index) => (
                <li key={`${row.at}-${index}`}>
                  <time dateTime={row.at}>
                    {new Date(row.at).toLocaleString(lang, {
                      day: "numeric",
                      month: "short",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </time>
                  <span>
                    {t(`console.consent_${row.purpose}` as Key)}:{" "}
                    {row.granted ? t("console.consent_yes") : t("console.consent_no")}
                  </span>
                  <span className="pay-note" dir="auto">
                    {row.who || t("console.customer_label")}
                  </span>
                  <details>
                    <summary>{t(`console.consent_via.${row.via}` as Key)}</summary>
                    <p dir="auto">{row.wording}</p>
                  </details>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </section>
  );
}
