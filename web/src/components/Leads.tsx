// Where customers came from (features 78 and 81): replies to the shop's stories, stories that
// mention it, the ads and links that opened chats, and the orders each brought.
import { useCallback } from "react";
import { api } from "../api";
import type { OwnerMe } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 60_000;

export function LeadsPanel({ me, token }: { me: OwnerMe; token: string }) {
  const { t, lang } = useLang();
  const load = useCallback(() => api.leads(token), [token]);
  const { data } = usePolled(load, POLL_MS);
  if (!data || (data.leads.length === 0 && data.brought.length === 0)) return null;
  return (
    <section className="pay-panel leads" aria-labelledby="leads-title">
      <h2 id="leads-title" className="section-title">
        {t("console.leads_title")}
      </h2>
      <p className="aside">{t("console.leads_lede")}</p>
      <div className="pay-columns">
        {data.brought.length > 0 && (
          <div className="pay-block">
            <h3 className="pay-block-title">{t("console.leads_brought")}</h3>
            <table className="daybook">
              <thead>
                <tr>
                  <th scope="col">{t("console.leads_source")}</th>
                  <th scope="col">{t("console.leads_chats")}</th>
                  <th scope="col">{t("console.leads_orders")}</th>
                  <th scope="col">{t("console.leads_value")}</th>
                </tr>
              </thead>
              <tbody>
                {data.brought.map((row) => (
                  <tr key={`${row.kind}-${row.ref}`}>
                    <th scope="row" dir="auto">
                      {t(`console.lead_kind.${row.kind}` as Key)}
                      {(row.title || row.ref) && `: ${row.title || row.ref}`}
                    </th>
                    <td>{row.chats}</td>
                    <td>{row.orders}</td>
                    <td>{formatMoney(row.value, me.country, lang)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pay-block">
          <h3 className="pay-block-title">{t("console.leads_recent")}</h3>
          <ul className="pay-rows">
            {data.leads.map((lead, index) => (
              <li key={`${lead.at}-${index}`}>
                <span>{t(`console.lead_kind.${lead.kind}` as Key)}</span>
                <span dir="auto">{lead.customer || t("console.customer_label")}</span>
                {lead.text && (
                  <span className="pay-note" dir="auto">
                    {lead.text}
                  </span>
                )}
                {lead.url && (
                  <a href={lead.url} target="_blank" rel="noopener noreferrer">
                    {t("console.lead_open")}
                  </a>
                )}
                {lead.ordered && <span className="pay-on">{t("console.lead_ordered")}</span>}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
