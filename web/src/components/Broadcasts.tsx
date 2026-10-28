// Broadcasts (feature 15): the owner's words to the customers who showed interest in a product
// or a kind of product and said yes to offers, at most one a week each, and what each one
// brought back. Code picks who hears it and counts the orders; the owner writes the words.
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type { Audience, BroadcastAbout, BroadcastResult, OwnerMe, OwnerProduct } from "../api";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 30_000;
const MIN_TEXT = 5;
const MAX_TEXT = 600;

/** Who would hear a broadcast about this, asked again whenever the choice changes. */
function useAudience(token: string, about: BroadcastAbout, sent: number) {
  const [audience, setAudience] = useState<Audience | null>(null);
  const wanted = JSON.stringify(about);
  useEffect(() => {
    let current = true;
    api.audience(token, JSON.parse(wanted) as BroadcastAbout).then(
      (found) => current && setAudience(found),
      () => current && setAudience(null),
    );
    return () => {
      current = false;
    };
  }, [token, wanted, sent]);
  return audience;
}

function ResultRow({ me, result }: { me: OwnerMe; result: BroadcastResult }) {
  const { t, lang } = useLang();
  return (
    <li>
      <p className="broadcast-text" dir="auto">
        {result.text}
      </p>
      <span className="pay-note" dir="auto">
        {result.product ?? result.category ?? t("console.broadcast_everyone")}
      </span>
      <span className="pay-note">
        {t("console.broadcast_reached", {
          sent: result.sent + result.templated,
          closed: result.closed,
        })}
      </span>
      <span className="pay-amount">
        {t("console.broadcast_return", {
          orders: result.orders,
          value: formatMoney(result.value, me.country, lang),
        })}
      </span>
      {result.product && (
        <span className="pay-note">
          {t("console.broadcast_with_product", { count: result.with_product })}
        </span>
      )}
    </li>
  );
}

export function BroadcastsPanel({
  me,
  token,
  products,
}: {
  me: OwnerMe;
  token: string;
  products: OwnerProduct[];
}) {
  const { t } = useLang();
  const load = useCallback(() => api.broadcasts(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [product, setProduct] = useState("");
  const [category, setCategory] = useState("");
  const [text, setText] = useState("");
  const [sent, setSent] = useState(0);
  const [state, setState] = useState<"idle" | "sending" | "sent" | "failed">("idle");
  const about: BroadcastAbout = { product_code: product || null, category: category || null };
  const audience = useAudience(token, about, sent);

  async function send(event: FormEvent) {
    event.preventDefault();
    setState("sending");
    try {
      await api.broadcast(token, about, text.trim());
      setText("");
      setSent(sent + 1);
      setState("sent");
      refresh();
    } catch {
      setState("failed");
    }
  }

  if (!data) return null;
  const onSale = products.filter((p) => p.status === "active");
  return (
    <section className="pay-panel broadcasts" aria-labelledby="broadcasts-title">
      <h2 id="broadcasts-title" className="section-title">
        {t("console.broadcasts_title")}
      </h2>
      <p className="aside">{t("console.broadcasts_lede")}</p>
      <div className="pay-columns">
        <form className="pay-block books-form" onSubmit={(event) => void send(event)}>
          <label>
            <span>{t("console.broadcast_product")}</span>
            <select
              value={product}
              onChange={(e) => {
                setProduct(e.target.value);
                if (e.target.value) setCategory("");
              }}
            >
              <option value="">{t("console.broadcast_any")}</option>
              {onSale.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          {!product && data.categories.length > 0 && (
            <label>
              <span>{t("console.broadcast_category")}</span>
              <select value={category} onChange={(e) => setCategory(e.target.value)}>
                <option value="">{t("console.broadcast_any")}</option>
                {data.categories.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            <span>{t("console.broadcast_text")}</span>
            <textarea
              value={text}
              rows={4}
              maxLength={MAX_TEXT}
              dir="auto"
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          {audience && (
            <p className="pay-note" role="status">
              {t("console.broadcast_audience", {
                interested: audience.interested,
                agreed: audience.agreed,
              })}
            </p>
          )}
          {!data.template && <p className="aside">{t("console.broadcast_no_template")}</p>}
          <div className="wording-actions">
            <button
              type="submit"
              className="button button-ink button-small"
              disabled={state === "sending" || text.trim().length < MIN_TEXT || !audience?.agreed}
            >
              {t("console.broadcast_send", { count: audience?.agreed ?? 0 })}
            </button>
            {state === "sent" && <span role="status">{t("console.broadcast_sent")}</span>}
            {state === "failed" && <span role="alert">{t("console.offers_failed")}</span>}
          </div>
        </form>
        <div className="pay-block">
          <h3 id="broadcast-results-title" className="pay-block-title">
            {t("console.broadcast_results")}
          </h3>
          {data.results.length === 0 ? (
            <p className="aside">{t("console.broadcast_none")}</p>
          ) : (
            <ul className="pay-rows" aria-labelledby="broadcast-results-title">
              {data.results.map((result) => (
                <ResultRow key={result.created_at} me={me} result={result} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
