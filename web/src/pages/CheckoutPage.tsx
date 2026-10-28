// The shop page's checkout (feature 80): where a Facebook or Instagram shop link lands
// (?products=<id>:<qty>,...&cart_origin=instagram) and where the shop page's cart goes. Prices,
// the delivery charge and the total come from the API's quote; the order is placed by the API
// alone and its payment instructions also go to the buyer's chat with the shop.
import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api";
import type { DeliveryDetails, Placed, Quote } from "../api";
import { parseProducts, sourceOf, useCart, writeCart } from "../cart";
import { Page, Problem } from "../components/Shell";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { useServer } from "../server";
import { chatToken } from "../useChat";
import { useShop } from "./ShopPage";

const QUOTE_DELAY_MS = 400;
const EMPTY: DeliveryDetails = {
  name: "",
  phone: "",
  city: "",
  address: "",
  area: "",
  landmark: "",
};
const FIELDS: { name: keyof DeliveryDetails; key: Key; required: boolean; type?: string }[] = [
  { name: "name", key: "checkout.name", required: true },
  { name: "phone", key: "checkout.phone", required: true, type: "tel" },
  { name: "city", key: "checkout.city", required: true },
  { name: "address", key: "checkout.address", required: true },
  { name: "area", key: "checkout.area", required: true },
  { name: "landmark", key: "checkout.landmark", required: false },
];
const FIELD_ERRORS: Record<string, keyof DeliveryDetails> = {
  address_vague: "area",
  phone_invalid: "phone",
};

/** The quote for the cart and city, asked again a moment after either changes. */
function useQuote(slug: string, lines: { item: string; quantity: number }[], city: string) {
  const { state } = useServer();
  const [quote, setQuote] = useState<Quote | null>(null);
  const [failed, setFailed] = useState(false);
  const wanted = JSON.stringify([lines, city.trim()]);
  useEffect(() => {
    if (state !== "online" || lines.length === 0) return;
    let current = true;
    const timer = window.setTimeout(() => {
      api.quote(slug, lines, city).then(
        (fresh) => {
          if (!current) return;
          setQuote(fresh);
          setFailed(false);
        },
        () => current && setFailed(true),
      );
    }, QUOTE_DELAY_MS);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
    // `wanted` stands for the lines and the city by value: the same cart asks only once.
  }, [slug, state, wanted]);
  return { quote, failed };
}

function Confirmation({ slug, placed }: { slug: string; placed: Placed }) {
  const { t } = useLang();
  return (
    <section className="checkout-done" aria-labelledby="checkout-done-title">
      <h1 id="checkout-done-title" className="title">
        {t("checkout.placed_title", { code: placed.code })}
      </h1>
      <p className="checkout-message" dir="auto">
        {placed.message}
      </p>
      <p className="pay-note">{t("checkout.chat_note")}</p>
      <div className="checkout-done-actions">
        <Link to={`/s/${slug}/chat`} className="button button-ink">
          {t("checkout.continue_chat")}
        </Link>
        <Link to={`/s/${slug}`} className="action">
          {t("checkout.back")}
        </Link>
      </div>
    </section>
  );
}

export function CheckoutPage() {
  const { slug = "" } = useParams();
  const [params] = useSearchParams();
  const { t, lang } = useLang();
  const { profile, missing } = useShop(slug);
  const cart = useCart(slug);
  const [source] = useState(() => sourceOf(params.get("cart_origin")));
  const [details, setDetails] = useState<DeliveryDetails>(EMPTY);
  const [method, setMethod] = useState<"cod" | "transfer" | null>(null);
  const [state, setState] = useState<"idle" | "placing" | string>("idle");
  const [placed, setPlaced] = useState<Placed | null>(null);
  const { quote, failed } = useQuote(slug, cart.lines, details.city);
  const country = profile?.country;
  const money = (minor: number) => formatMoney(minor, country, lang);
  const linked = params.get("products");

  // A shop link's products become the cart, once.
  useEffect(() => {
    const lines = parseProducts(linked);
    if (lines.length > 0) writeCart(slug, lines);
  }, [slug, linked]);

  const methods = quote?.methods ?? [];
  const chosen = method && methods.includes(method) ? method : (methods[0] ?? null);
  const ready = useMemo(
    () => quote !== null && quote.lines.every((line) => line.problem === null),
    [quote],
  );

  async function place(event: FormEvent) {
    event.preventDefault();
    if (!chosen || !ready) return;
    setState("placing");
    try {
      const token = await chatToken(slug);
      const order = await api.checkout(slug, token, {
        lines: cart.lines,
        details,
        payment_method: chosen,
        source,
        lang,
      });
      writeCart(slug, []);
      setPlaced(order);
    } catch (error) {
      setState(error instanceof ApiError ? error.code : "failed");
    }
  }

  if (missing) {
    return (
      <Page>
        <Problem message={t("shop.not_found")} />
      </Page>
    );
  }
  if (placed) {
    return (
      <Page>
        <Confirmation slug={slug} placed={placed} />
      </Page>
    );
  }
  const fieldError = FIELD_ERRORS[state];
  return (
    <Page>
      <section className="checkout" aria-labelledby="checkout-title">
        <h1 id="checkout-title" className="title">
          {t("checkout.title")}
        </h1>
        {profile && (
          <p className="lede" dir="auto">
            {profile.name}
          </p>
        )}
        {source !== "shop_page" && (
          <p className="checkout-source">{t(`checkout.from_${source}` as Key)}</p>
        )}
        {cart.lines.length === 0 ? (
          <div className="checkout-empty">
            <p>{t("checkout.empty")}</p>
            <Link to={`/s/${slug}`} className="button button-ink">
              {t("checkout.back")}
            </Link>
          </div>
        ) : (
          <form className="checkout-grid" onSubmit={(event) => void place(event)}>
            <div className="pay-block checkout-cart" aria-busy={!quote}>
              <h2 className="pay-block-title">{t("checkout.cart")}</h2>
              <ul className="checkout-lines">
                {cart.lines.map((line) => {
                  const shown = quote?.lines.find((q) => q.item === line.item);
                  const name = shown?.name ?? line.item;
                  const options = Object.values(shown?.options ?? {}).join(" ");
                  return (
                    <li key={line.item} className={shown?.problem ? "is-problem" : undefined}>
                      <span className="checkout-line-name" dir="auto">
                        {name}
                        {options && <span className="checkout-line-options"> {options}</span>}
                      </span>
                      <span className="checkout-qty">
                        <button
                          type="button"
                          aria-label={t("checkout.less", { name })}
                          onClick={() => cart.set(line.item, line.quantity - 1)}
                        >
                          −
                        </button>
                        <output aria-live="polite">{line.quantity}</output>
                        <button
                          type="button"
                          aria-label={t("checkout.more", { name })}
                          onClick={() => cart.set(line.item, line.quantity + 1)}
                        >
                          +
                        </button>
                      </span>
                      <span className="checkout-line-total">
                        {shown && !shown.problem ? money(shown.line_total) : ""}
                      </span>
                      {shown?.problem && (
                        <span className="checkout-line-problem" role="alert">
                          {t(`checkout.problem.${shown.problem}` as Key, { left: shown.left ?? 0 })}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
              {quote && (
                <dl className="dash-rows checkout-sums">
                  <div>
                    <dt>{t("checkout.subtotal")}</dt>
                    <dd>{money(quote.subtotal)}</dd>
                  </div>
                  <div>
                    <dt>{t("checkout.delivery")}</dt>
                    <dd>
                      {quote.free_delivery
                        ? t("checkout.delivery_free")
                        : quote.delivery !== null
                          ? money(quote.delivery)
                          : details.city.trim()
                            ? t("checkout.delivery_later")
                            : t("checkout.delivery_city")}
                    </dd>
                  </div>
                  <div className="checkout-total">
                    <dt>{t("checkout.total")}</dt>
                    <dd>{money(quote.total)}</dd>
                  </div>
                </dl>
              )}
              {failed && <Problem message={t("checkout.err.failed")} />}
            </div>
            <div className="pay-block checkout-details">
              <h2 className="pay-block-title">{t("checkout.details")}</h2>
              <div className="books-form">
                {FIELDS.map((field) => (
                  <label key={field.name}>
                    <span>{t(field.key)}</span>
                    <input
                      name={field.name}
                      type={field.type ?? "text"}
                      value={details[field.name]}
                      required={field.required}
                      maxLength={field.name === "address" ? 200 : 120}
                      dir={field.type === "tel" ? "ltr" : "auto"}
                      autoComplete={
                        field.name === "name"
                          ? "name"
                          : field.name === "phone"
                            ? "tel"
                            : field.name === "city"
                              ? "address-level2"
                              : field.name === "address"
                                ? "address-line1"
                                : "off"
                      }
                      aria-invalid={fieldError === field.name || undefined}
                      onChange={(e) => setDetails({ ...details, [field.name]: e.target.value })}
                    />
                    {fieldError === field.name && (
                      <span className="field-error" role="alert">
                        {t(`checkout.err.${state}` as Key)}
                      </span>
                    )}
                  </label>
                ))}
              </div>
              {methods.length > 0 && (
                <fieldset className="checkout-pay">
                  <legend>{t("checkout.pay")}</legend>
                  {methods.map((option) => (
                    <label key={option}>
                      <input
                        type="radio"
                        name="payment"
                        value={option}
                        checked={chosen === option}
                        onChange={() => setMethod(option)}
                      />
                      {t(`checkout.${option}` as Key)}
                    </label>
                  ))}
                </fieldset>
              )}
              <button
                type="submit"
                className="button button-saffron checkout-place"
                disabled={!ready || !chosen || state === "placing"}
              >
                {state === "placing" ? t("checkout.placing") : t("checkout.place")}
              </button>
              {state !== "idle" && state !== "placing" && !fieldError && (
                <Problem
                  message={t(
                    state === "cart_problem" ? "checkout.err.cart_problem" : "checkout.err.failed",
                  )}
                />
              )}
            </div>
          </form>
        )}
      </section>
    </Page>
  );
}
