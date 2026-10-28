// Suppliers (feature 53): what each supplier charged for the same item, from the shop's
// restocks, and one reorder message per supplier for what is running low. Nothing is sent from
// here: the owner opens their own WhatsApp with the message typed, or copies it.
import { useCallback } from "react";
import { api } from "../api";
import type { OwnerMe, SupplierReorder } from "../api";
import { useCopy } from "../clipboard";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 60_000;

function Reorder({ me, reorder }: { me: OwnerMe; reorder: SupplierReorder }) {
  const { t, lang } = useLang();
  const { copied, copy } = useCopy();
  return (
    <li className="supplier-reorder">
      <h4 dir="auto">{reorder.supplier ?? t("console.suppliers_unknown")}</h4>
      <ul className="supplier-lines">
        {reorder.lines.map((line) => (
          <li key={line.name}>
            <span dir="auto">{line.name}</span>
            <span>× {line.quantity}</span>
          </li>
        ))}
      </ul>
      {reorder.total !== null && (
        <p className="pay-note">
          {t("console.suppliers_total", { amount: formatMoney(reorder.total, me.country, lang) })}
        </p>
      )}
      <div className="supplier-actions">
        {reorder.link && (
          <a className="action action-primary" href={reorder.link} target="_blank" rel="noreferrer">
            {t("console.suppliers_open")}
          </a>
        )}
        <button type="button" className="action" onClick={() => void copy(reorder.draft)}>
          {copied ? t("console.copied") : t("console.copy")}
        </button>
      </div>
    </li>
  );
}

export function SuppliersPanel({ me, token }: { me: OwnerMe; token: string }) {
  const { t, lang } = useLang();
  const load = useCallback(() => api.suppliers(token), [token]);
  const { data } = usePolled(load, POLL_MS);
  if (!data) return null;
  const money = (minor: number) => formatMoney(minor, me.country, lang);
  const day = (iso: string) =>
    new Date(`${iso}T00:00:00Z`).toLocaleDateString(lang, { dateStyle: "medium", timeZone: "UTC" });
  return (
    <section className="pay-panel suppliers" aria-labelledby="suppliers-title">
      <h2 id="suppliers-title" className="section-title">
        {t("console.suppliers_title")}
      </h2>
      <p className="aside">{t("console.suppliers_lede")}</p>
      <h3 className="pay-block-title">{t("console.suppliers_reorders")}</h3>
      {data.reorders.length === 0 ? (
        <p className="aside">{t("console.suppliers_no_low")}</p>
      ) : (
        <ul className="supplier-reorders">
          {data.reorders.map((reorder) => (
            <Reorder key={reorder.supplier ?? ""} me={me} reorder={reorder} />
          ))}
        </ul>
      )}
      <h3 className="pay-block-title">{t("console.suppliers_compare")}</h3>
      {data.compared.length === 0 ? (
        <p className="aside">{t("console.suppliers_compare_none")}</p>
      ) : (
        <div className="table-scroll">
          <table className="supplier-table">
            <thead>
              <tr>
                <th scope="col">{t("console.suppliers_item")}</th>
                <th scope="col">{t("console.suppliers_supplier")}</th>
                <th scope="col">{t("console.suppliers_unit")}</th>
                <th scope="col">{t("console.suppliers_when")}</th>
              </tr>
            </thead>
            <tbody>
              {data.compared.flatMap((item) =>
                item.quotes.map((quote, index) => (
                  <tr key={`${item.variant_id}-${quote.supplier}`}>
                    {index === 0 && (
                      <th scope="row" rowSpan={item.quotes.length} dir="auto">
                        {item.name}
                      </th>
                    )}
                    <td dir="auto">
                      {quote.supplier}
                      {index === 0 && (
                        <span className="supplier-cheapest">{t("console.suppliers_cheapest")}</span>
                      )}
                    </td>
                    <td>{money(quote.unit_cost)}</td>
                    <td>{day(quote.on)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
