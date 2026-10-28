// Custom orders (feature 17): what the customer asked to have made, the sketch code drew from
// it, and the owner's price. The customer accepts or declines the price in their chat; an
// accepted one goes into their cart at that price.
import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, api } from "../api";
import type { CustomRequest, OwnerMe } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney, parseAmount, toMinor } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 20_000;
const MAKERS = new Set(["food", "clothing", "services"]);
const OPEN = new Set(["waiting", "quoted"]);

function QuoteForm({
  me,
  token,
  item,
  onSent,
}: {
  me: OwnerMe;
  token: string;
  item: CustomRequest;
  onSent: () => void;
}) {
  const { t } = useLang();
  const [price, setPrice] = useState("");
  const [note, setNote] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "bad_price" | "failed">("idle");

  async function send(event: FormEvent) {
    event.preventDefault();
    const amount = parseAmount(price);
    if (amount === null || amount <= 0) {
      setState("bad_price");
      return;
    }
    setState("sending");
    try {
      await api.quoteCustom(token, item.id, toMinor(amount, me.country), note.trim());
      setState("idle");
      setPrice("");
      onSent();
    } catch (error) {
      setState(error instanceof ApiError && error.status === 422 ? "bad_price" : "failed");
    }
  }

  return (
    <form className="custom-quote" onSubmit={(event) => void send(event)}>
      <label>
        <span>{t("console.custom_price")}</span>
        <input
          value={price}
          required
          inputMode="decimal"
          maxLength={12}
          dir="ltr"
          onChange={(e) => setPrice(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.custom_note")}</span>
        <input value={note} maxLength={300} dir="auto" onChange={(e) => setNote(e.target.value)} />
      </label>
      <button
        type="submit"
        className="button button-ink button-small"
        disabled={state === "sending"}
      >
        {item.status === "quoted" ? t("console.custom_requote") : t("console.custom_send")}
      </button>
      {state === "bad_price" && <span role="alert">{t("console.custom_bad_price")}</span>}
      {state === "failed" && <span role="alert">{t("console.offers_failed")}</span>}
    </form>
  );
}

function RequestCard({
  me,
  token,
  item,
  canPrice,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  item: CustomRequest;
  canPrice: boolean;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  const { text, colours, size, needed_by: neededBy, notes } = item.details;
  const facts: [Key, string | undefined][] = [
    ["console.custom_text", text],
    ["console.custom_colours", colours?.join(", ")],
    ["console.custom_size", size],
    ["console.custom_needed_by", neededBy],
    ["console.custom_notes", notes],
  ];
  return (
    <li className={`custom-card is-${item.status}`}>
      {item.sketch_url ? (
        <a className="pay-shot" href={item.sketch_url} target="_blank" rel="noopener noreferrer">
          <img src={item.sketch_url} alt={t("console.custom_sketch", { code: item.code })} />
        </a>
      ) : (
        <p className="pay-note">{t("console.custom_drawing")}</p>
      )}
      <div className="pay-body">
        <p className="waiting-head">
          <strong>{t(`custom.kind.${item.kind}` as Key)}</strong>
          <span className="pay-code">{item.code}</span>
          <span dir="auto">{item.customer || t("console.customer_label")}</span>
          <span className={`order-status status-${item.status}`}>
            {t(`console.custom_status.${item.status}` as Key)}
          </span>
        </p>
        <dl className="custom-facts">
          {facts
            .filter(([, value]) => value)
            .map(([label, value]) => (
              <div key={label}>
                <dt>{t(label)}</dt>
                <dd dir="auto">{value}</dd>
              </div>
            ))}
        </dl>
        {item.price !== null && (
          <p className="pay-note">
            {t("console.custom_quoted", { price: formatMoney(item.price, me.country, lang) })}
          </p>
        )}
        {canPrice && OPEN.has(item.status) && (
          <QuoteForm me={me} token={token} item={item} onSent={onChange} />
        )}
      </div>
    </li>
  );
}

export function CustomRequestsPanel({
  me,
  token,
  canPrice,
}: {
  me: OwnerMe;
  token: string;
  canPrice: boolean;
}) {
  const { t } = useLang();
  const load = useCallback(() => api.customRequests(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  if (!data || (!MAKERS.has(me.template) && data.length === 0)) return null;
  const waiting = data.filter((item) => item.status === "waiting").length;
  return (
    <section className="pay-panel custom-requests" aria-labelledby="custom-title">
      <h2 id="custom-title" className="section-title">
        {t("console.custom_title")}
        {waiting > 0 && <span className="console-count">{waiting}</span>}
      </h2>
      <p className="aside">{t("console.custom_lede")}</p>
      {data.length === 0 ? (
        <p className="aside">{t("console.custom_none")}</p>
      ) : (
        <ul className="pay-rows">
          {data.map((item) => (
            <RequestCard
              key={item.id}
              me={me}
              token={token}
              item={item}
              canPrice={canPrice}
              onChange={refresh}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
