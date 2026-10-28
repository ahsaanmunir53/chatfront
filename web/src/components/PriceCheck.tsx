// Prices elsewhere (feature 56): similar items at other Chatfront shops in the same currency,
// as a count and a spread, never which shops. A web search of other sellers needs the Meta
// Model API, so the panel says plainly that this is not the whole market.
import { useState } from "react";
import { api } from "../api";
import type { OwnerMe, PriceElsewhere } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";

export function PriceCheckPanel({ me, token }: { me: OwnerMe; token: string }) {
  const { t, lang } = useLang();
  const [found, setFound] = useState<PriceElsewhere[] | null>(null);
  const [state, setState] = useState<"idle" | "checking" | "failed">("idle");
  const money = (minor: number) => formatMoney(minor, me.country, lang);

  async function check() {
    setState("checking");
    try {
      setFound(await api.priceCheck(token));
      setState("idle");
    } catch {
      setState("failed");
    }
  }

  return (
    <section className="pay-panel price-check" aria-labelledby="price-check-title">
      <h2 id="price-check-title" className="section-title">
        {t("console.price_check_title")}
      </h2>
      <p className="aside">{t("console.price_check_lede")}</p>
      <button
        type="button"
        className="action"
        disabled={state === "checking"}
        onClick={() => void check()}
      >
        {t("console.price_check_run")}
      </button>
      {state === "failed" && <p role="alert">{t("console.offers_failed")}</p>}
      {found && (
        <div className="table-scroll">
          <table className="supplier-table">
            <thead>
              <tr>
                <th scope="col">{t("console.price_check_item")}</th>
                <th scope="col">{t("console.price_check_yours")}</th>
                <th scope="col">{t("console.price_check_others")}</th>
              </tr>
            </thead>
            <tbody>
              {found.map((item) => (
                <tr key={item.product_id}>
                  <th scope="row" dir="auto">
                    {item.name}
                  </th>
                  <td>{money(item.price)}</td>
                  <td>
                    {item.shops === 0 || item.lowest === null ? (
                      <span className="pay-note">{t("console.price_check_none")}</span>
                    ) : (
                      <>
                        <span>{t("console.price_check_shops", { count: item.shops })}: </span>
                        <span>
                          {t("console.price_check_range", {
                            lowest: money(item.lowest),
                            highest: money(item.highest ?? item.lowest),
                            middle: money(item.middle ?? item.lowest),
                          })}
                        </span>
                        {item.place && (
                          <span className="supplier-cheapest">
                            {t(`console.price_check_${item.place}` as Key)}
                          </span>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
