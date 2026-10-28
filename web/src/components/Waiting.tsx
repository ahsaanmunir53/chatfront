// Customers waiting for something sold out (feature 16): they are told in their chat when it
// is back; the ones a closed WhatsApp window kept from hearing are marked for the owner.
import { useCallback } from "react";
import { api } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 30_000;

export function WaitingPanel({ token }: { token: string }) {
  const { t, lang } = useLang();
  const load = useCallback(() => api.waitlist(token), [token]);
  const { data } = usePolled(load, POLL_MS);
  if (!data || data.length === 0) return null;
  return (
    <section className="pay-panel waiting-stock" aria-labelledby="waiting-stock-title">
      <h2 id="waiting-stock-title" className="section-title">
        {t("console.waitlist_title")}
        <span className="console-count">{data.length}</span>
      </h2>
      <p className="aside">{t("console.waitlist_lede")}</p>
      <ul className="pay-rows pay-block">
        {data.map((entry) => (
          <li key={entry.id} className={entry.status === "missed" ? "is-missed" : undefined}>
            <span className="pay-code">{entry.product_code}</span>
            <span dir="auto">
              {entry.quantity > 1 ? `${entry.quantity} × ${entry.name}` : entry.name}
            </span>
            <span className="pay-note" dir="auto">
              {entry.customer || t("console.customer_label")}
            </span>
            <span className="pay-note">
              {new Date(entry.created_at).toLocaleDateString(lang, {
                day: "numeric",
                month: "short",
              })}
            </span>
            <span className={`waiting-status is-${entry.status}`}>
              {t(`console.waitlist_status.${entry.status}` as Key)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
