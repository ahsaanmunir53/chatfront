// Flash and live sales (feature 8): a sale price on a few products for a while, the pieces to
// whoever takes them first. In a live sale each product gets the number the owner says on the
// live, and customers write it in the chat or as a comment to take one. Code holds each piece
// for the customer's cart; this panel shows what is left, held and ordered.
import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, api } from "../api";
import type { OwnerMe, OwnerProduct, OwnerSale, SaleItemIn } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney, parseAmount, toMinor } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 15_000;
const DURATIONS = [30, 60, 120, 240, 1440];
const STARTS = [0, 30, 60, 1440];
const HOLDS = [10, 15, 30];
const MAX_ITEMS = 20;

type Draft = { price: string; units: string; perCustomer: string };

/** "30 minutes", "2 hours": a length of time as the owner says it. */
function useSpan() {
  const { t } = useLang();
  return (minutes: number) =>
    minutes < 60
      ? t("console.sale_minutes", { minutes })
      : t("console.sale_hours", { hours: Math.round(minutes / 60) });
}

function SaleCard({
  me,
  token,
  sale,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  sale: OwnerSale;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  const money = (minor: number) => formatMoney(minor, me.country, lang);
  const until = new Date(sale.ends_at).toLocaleString(lang, {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
  return (
    <li className={`sale-card is-${sale.state}`}>
      <p>
        <strong dir="auto">{sale.name}</strong>{" "}
        <span className="pay-note">
          {t(`console.sale_state.${sale.state}` as Key, { when: until })}
        </span>
        {sale.live && <span className="order-badge">{t("console.sale_live")}</span>}
      </p>
      <table className="daybook">
        <thead>
          <tr>
            {sale.live && <th scope="col">#</th>}
            <th scope="col">{t("console.sale_product")}</th>
            <th scope="col">{t("console.sale_price")}</th>
            <th scope="col">{t("console.sale_left")}</th>
            <th scope="col">{t("console.sale_held")}</th>
            <th scope="col">{t("console.sale_ordered")}</th>
          </tr>
        </thead>
        <tbody>
          {sale.items.map((item) => (
            <tr key={item.product_code}>
              {sale.live && <td>{item.number}</td>}
              <th scope="row" dir="auto">
                {item.name}
              </th>
              <td>
                {money(item.price)} <s className="pay-note">{money(item.list_price)}</s>
              </td>
              <td>
                {item.left} / {item.units}
              </td>
              <td>{item.held}</td>
              <td>{item.ordered}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {sale.state !== "ended" && (
        <button
          type="button"
          className="action offer-stop"
          onClick={() => void api.stopSale(token, sale.id).then(onChange, onChange)}
        >
          {t("console.sale_stop")}
        </button>
      )}
    </li>
  );
}

function SaleForm({
  me,
  token,
  products,
  onSaved,
}: {
  me: OwnerMe;
  token: string;
  products: OwnerProduct[];
  onSaved: () => void;
}) {
  const { t, lang } = useLang();
  const [name, setName] = useState("");
  const [live, setLive] = useState(false);
  const [startsIn, setStartsIn] = useState(0);
  const [minutes, setMinutes] = useState(60);
  const [hold, setHold] = useState(15);
  const [picked, setPicked] = useState<Record<string, Draft>>({});
  const [state, setState] = useState<"idle" | "saving" | "saved" | string>("idle");
  const span = useSpan();
  const onSale = products.filter((p) => p.status === "active");

  function toggle(code: string) {
    const next = { ...picked };
    if (next[code]) delete next[code];
    else if (Object.keys(next).length < MAX_ITEMS)
      next[code] = { price: "", units: "1", perCustomer: "1" };
    setPicked(next);
  }

  function items(): SaleItemIn[] | null {
    const out: SaleItemIn[] = [];
    for (const [code, draft] of Object.entries(picked)) {
      const price = parseAmount(draft.price);
      if (price === null || price <= 0) return null;
      out.push({
        product_code: code,
        price: toMinor(price, me.country),
        units: Math.max(1, Math.round(Number(draft.units) || 1)),
        per_customer: Math.max(1, Math.round(Number(draft.perCustomer) || 1)),
      });
    }
    return out.length > 0 ? out : null;
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const chosen = items();
    if (!chosen) {
      setState("sale_items");
      return;
    }
    setState("saving");
    try {
      await api.startSale(token, {
        name: name.trim(),
        live,
        starts_in_minutes: startsIn,
        minutes,
        hold_minutes: hold,
        items: chosen,
      });
      setPicked({});
      setName("");
      setState("saved");
      onSaved();
    } catch (error) {
      setState(error instanceof ApiError ? error.code : "failed");
    }
  }

  const known = ["sale_items", "sale_not_lower", "sale_window"];
  return (
    <form className="books-form" onSubmit={(event) => void save(event)}>
      <h3 className="pay-block-title">{t("console.sale_new")}</h3>
      <label>
        <span>{t("console.sale_name")}</span>
        <input
          value={name}
          required
          minLength={2}
          maxLength={80}
          dir="auto"
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="offer-check">
        <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} />
        <span>{t("console.sale_live_mode")}</span>
      </label>
      {live && <p className="aside">{t("console.sale_live_note")}</p>}
      <label>
        <span>{t("console.sale_starts")}</span>
        <select value={startsIn} onChange={(e) => setStartsIn(Number(e.target.value))}>
          {STARTS.map((value) => (
            <option key={value} value={value}>
              {value === 0 ? t("console.sale_now") : t("console.sale_in", { span: span(value) })}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{t("console.sale_lasts")}</span>
        <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
          {DURATIONS.map((value) => (
            <option key={value} value={value}>
              {span(value)}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{t("console.sale_hold")}</span>
        <select value={hold} onChange={(e) => setHold(Number(e.target.value))}>
          {HOLDS.map((value) => (
            <option key={value} value={value}>
              {span(value)}
            </option>
          ))}
        </select>
      </label>
      <fieldset className="sale-products">
        <legend>{t("console.sale_products")}</legend>
        {onSale.map((product) => {
          const draft = picked[product.code];
          return (
            <div key={product.code} className="sale-product">
              <label className="offer-check">
                <input
                  type="checkbox"
                  checked={Boolean(draft)}
                  onChange={() => toggle(product.code)}
                />
                <span dir="auto">
                  {product.name} ({formatMoney(product.price, me.country, lang)})
                </span>
              </label>
              {draft && (
                <div className="sale-numbers">
                  <label>
                    <span>{t("console.sale_price")}</span>
                    <input
                      value={draft.price}
                      required
                      inputMode="decimal"
                      maxLength={12}
                      dir="ltr"
                      onChange={(e) =>
                        setPicked({
                          ...picked,
                          [product.code]: { ...draft, price: e.target.value },
                        })
                      }
                    />
                  </label>
                  <label>
                    <span>{t("console.sale_units")}</span>
                    <input
                      value={draft.units}
                      inputMode="numeric"
                      maxLength={4}
                      dir="ltr"
                      onChange={(e) =>
                        setPicked({
                          ...picked,
                          [product.code]: { ...draft, units: e.target.value.replace(/\D/g, "") },
                        })
                      }
                    />
                  </label>
                  <label>
                    <span>{t("console.sale_each")}</span>
                    <input
                      value={draft.perCustomer}
                      inputMode="numeric"
                      maxLength={2}
                      dir="ltr"
                      onChange={(e) =>
                        setPicked({
                          ...picked,
                          [product.code]: {
                            ...draft,
                            perCustomer: e.target.value.replace(/\D/g, ""),
                          },
                        })
                      }
                    />
                  </label>
                </div>
              )}
            </div>
          );
        })}
      </fieldset>
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving"}
        >
          {t("console.sale_start")}
        </button>
        {state === "saved" && <span role="status">{t("console.saved")}</span>}
        {state !== "idle" && state !== "saving" && state !== "saved" && (
          <span role="alert">
            {t(known.includes(state) ? (`console.err.${state}` as Key) : "console.offers_failed")}
          </span>
        )}
      </div>
    </form>
  );
}

export function SalesPanel({
  me,
  token,
  products,
}: {
  me: OwnerMe;
  token: string;
  products: OwnerProduct[];
}) {
  const { t } = useLang();
  const load = useCallback(() => api.sales(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  if (!data) return null;
  return (
    <section className="pay-panel sales" aria-labelledby="sales-title">
      <h2 id="sales-title" className="section-title">
        {t("console.sales_title")}
      </h2>
      <p className="aside">{t("console.sales_lede")}</p>
      <div className="pay-columns">
        <div className="pay-block">
          {data.length === 0 ? (
            <p className="aside">{t("console.sales_none")}</p>
          ) : (
            <ul className="pay-rows">
              {data.map((sale) => (
                <SaleCard key={sale.id} me={me} token={token} sale={sale} onChange={refresh} />
              ))}
            </ul>
          )}
        </div>
        <div className="pay-block">
          <SaleForm me={me} token={token} products={products} onSaved={refresh} />
        </div>
      </div>
    </section>
  );
}
