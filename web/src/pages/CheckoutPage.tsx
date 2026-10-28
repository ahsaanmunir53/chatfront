// The shop page's checkout (feature 80): where a Facebook or Instagram shop link lands
// (?products=<id>:<qty>,...&cart_origin=instagram) and where the shop page's cart goes. Prices,
// the delivery charge and the total come from the API's quote; the order is placed by the API
// alone and its payment instructions also go to the buyer's chat with the shop. A buyer can
// send it as a gift with a card (feature 21), start a group order with friends, or add their
// part to a friend's (22); a friend's code from a shared link is put in for them (20).
import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api";
import type { CheckoutRequest, DeliveryDetails, GroupView, Placed, Quote } from "../api";
import { parseProducts, sourceOf, useCart, writeCart } from "../cart";
import { Page, Problem } from "../components/Shell";
import { ShareLink } from "../components/ShareLink";
import { BASE_PATH } from "../config";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { useServer } from "../server";
import { chatToken } from "../useChat";
import { forgetGroup, groupLink, readGroup, readRef } from "../visit";
import { dayLabel, useShop } from "./ShopPage";

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
// What the API refuses with a reason the buyer can act on.
const KNOWN_ERRORS = new Set([
  "cart_problem",
  "group_closed",
  "group_full",
  "gift_pays_transfer",
  "group_pays_transfer",
]);
const GROUP_DAYS = [1, 2, 3, 7];
const GIFT_MESSAGE = 200;
type Gift = { on: boolean; message: string; from: string };

/** The group order this buyer is adding a part to, when they came from its link. */
function useJoining(slug: string) {
  const [code, setCode] = useState(() => readGroup(slug));
  const [group, setGroup] = useState<GroupView | null>(null);
  useEffect(() => {
    if (!code) return;
    let current = true;
    api.group(slug, code, null).then(
      (found) => current && setGroup(found),
      () => current && setCode(null),
    );
    return () => {
      current = false;
    };
  }, [slug, code]);
  const leave = () => {
    forgetGroup(slug);
    setCode(null);
    setGroup(null);
  };
  return { code: group?.open ? code : null, group, leave };
}

/** The quote for the cart and city, asked again a moment after either changes. */
function useQuote(
  slug: string,
  lines: { item: string; quantity: number }[],
  city: string,
  coupon: string | null,
) {
  const { state } = useServer();
  const [quote, setQuote] = useState<Quote | null>(null);
  const [failed, setFailed] = useState(false);
  const wanted = JSON.stringify([lines, city.trim(), coupon]);
  useEffect(() => {
    if (state !== "online" || lines.length === 0) return;
    let current = true;
    const timer = window.setTimeout(() => {
      api.quote(slug, lines, city, coupon).then(
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
    // `wanted` stands for the lines, city and code by value: the same cart asks only once.
  }, [slug, state, wanted]);
  return { quote, failed };
}

function Confirmation({
  slug,
  placed,
  started,
}: {
  slug: string;
  placed: Placed;
  started: boolean;
}) {
  const { t } = useLang();
  const group = placed.group;
  return (
    <section className="checkout-done" aria-labelledby="checkout-done-title">
      <h1 id="checkout-done-title" className="title">
        {t("checkout.placed_title", { code: placed.code })}
      </h1>
      <p className="checkout-message" dir="auto">
        {placed.message}
      </p>
      <p className="pay-note">{t("checkout.chat_note")}</p>
      {group && started && (
        <div className="pay-block group-lead">
          <h2 className="pay-block-title">{t("group.share_title")}</h2>
          <p className="aside">{t("checkout.group_started")}</p>
          <ShareLink
            url={groupLink(window.location.origin, BASE_PATH, slug, group)}
            title={t("group.share_title")}
          />
        </div>
      )}
      {group && (
        <p>
          <Link to={`/s/${slug}/g/${group}`}>{t("group.see")}</Link>
        </p>
      )}
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
  const [code, setCode] = useState(() => readRef(slug) ?? "");
  const [coupon, setCoupon] = useState<string | null>(() => readRef(slug));
  const [gift, setGift] = useState<Gift>({ on: false, message: "", from: "" });
  const [withFriends, setWithFriends] = useState(false);
  const [days, setDays] = useState(2);
  const [member, setMember] = useState("");
  const joining = useJoining(slug);
  const city = joining.code ? "" : details.city;
  const { quote, failed } = useQuote(slug, cart.lines, city, coupon);
  const country = profile?.country;
  const money = (minor: number) => formatMoney(minor, country, lang);
  const linked = params.get("products");

  // A shop link's products become the cart, once.
  useEffect(() => {
    const lines = parseProducts(linked);
    if (lines.length > 0) writeCart(slug, lines);
  }, [slug, linked]);

  const offered = quote?.methods ?? [];
  // A gift and a friend's part of a group order are paid by transfer before they ship.
  const transferOnly = gift.on || joining.code !== null;
  const methods = transferOnly ? offered.filter((m) => m === "transfer") : offered;
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
      const request: CheckoutRequest = {
        lines: cart.lines,
        details: joining.code ? null : details,
        join: joining.code ? { code: joining.code, name: member.trim() } : null,
        start_group: !joining.code && withFriends ? days : null,
        gift: gift.on ? { message: gift.message.trim(), from_name: gift.from.trim() } : null,
        payment_method: chosen,
        coupon: quote?.coupon_problem ? null : coupon,
        source,
        lang,
      };
      const order = await api.checkout(slug, token, request);
      writeCart(slug, []);
      if (joining.code) forgetGroup(slug);
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
        <Confirmation slug={slug} placed={placed} started={withFriends && !joining.code} />
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
                      {shown?.ships_from && (
                        <span className="checkout-line-note">
                          {t("shop.preorder", { date: dayLabel(shown.ships_from, lang) })}
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
                      {joining.code
                        ? t("checkout.delivery_group")
                        : quote.free_delivery
                          ? t("checkout.delivery_free")
                          : quote.delivery !== null
                            ? money(quote.delivery)
                            : details.city.trim()
                              ? t("checkout.delivery_later")
                              : t("checkout.delivery_city")}
                    </dd>
                  </div>
                  {quote.offers.map((offer) => (
                    <div key={`${offer.kind}-${offer.label}`} className="checkout-offer">
                      <dt dir="auto">
                        {offer.kind === "coupon"
                          ? t("checkout.coupon_line", { code: offer.label })
                          : offer.label}
                      </dt>
                      <dd>−{money(offer.amount)}</dd>
                    </div>
                  ))}
                  <div className="checkout-total">
                    <dt>{t("checkout.total")}</dt>
                    <dd>{money(quote.total)}</dd>
                  </div>
                </dl>
              )}
              <div className="checkout-coupon">
                <label>
                  <span>{t("checkout.coupon")}</span>
                  <input
                    value={code}
                    maxLength={24}
                    dir="ltr"
                    autoCapitalize="characters"
                    onChange={(e) => setCode(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key !== "Enter") return;
                      e.preventDefault(); // Enter applies the code; it never places the order
                      if (code.trim()) setCoupon(code.trim().toUpperCase());
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="action"
                  disabled={!code.trim()}
                  onClick={() => setCoupon(code.trim().toUpperCase())}
                >
                  {t("checkout.coupon_apply")}
                </button>
                {coupon && quote?.coupon_problem && (
                  <span className="field-error" role="alert">
                    {t(`checkout.coupon_problem.${quote.coupon_problem}` as Key)}
                  </span>
                )}
              </div>
              {failed && <Problem message={t("checkout.err.failed")} />}
            </div>
            <div className="pay-block checkout-details">
              <h2 className="pay-block-title">{t("checkout.details")}</h2>
              {joining.code && joining.group && (
                <div className="visit-note">
                  <p>
                    {t("checkout.joining", {
                      name: joining.group.organiser,
                      city: joining.group.city,
                    })}
                  </p>
                  <button type="button" className="action" onClick={joining.leave}>
                    {t("checkout.leave_group")}
                  </button>
                </div>
              )}
              {joining.code ? (
                <div className="books-form">
                  <label>
                    <span>{t("checkout.your_name")}</span>
                    <input
                      value={member}
                      required
                      minLength={2}
                      maxLength={80}
                      dir="auto"
                      autoComplete="name"
                      onChange={(e) => setMember(e.target.value)}
                    />
                  </label>
                </div>
              ) : (
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
              )}
              {!joining.code && offered.includes("transfer") && (
                <fieldset className="checkout-extras">
                  <legend className="sr-only">{t("checkout.extras")}</legend>
                  <label className="offer-check">
                    <input
                      type="checkbox"
                      checked={gift.on}
                      onChange={(e) => setGift({ ...gift, on: e.target.checked })}
                    />
                    <span>{t("checkout.gift")}</span>
                  </label>
                  {gift.on && (
                    <div className="books-form">
                      <p className="aside">{t("checkout.gift_note")}</p>
                      <label>
                        <span>{t("checkout.gift_message")}</span>
                        <textarea
                          value={gift.message}
                          rows={3}
                          maxLength={GIFT_MESSAGE}
                          dir="auto"
                          onChange={(e) => setGift({ ...gift, message: e.target.value })}
                        />
                      </label>
                      <label>
                        <span>{t("checkout.gift_from")}</span>
                        <input
                          value={gift.from}
                          maxLength={80}
                          dir="auto"
                          onChange={(e) => setGift({ ...gift, from: e.target.value })}
                        />
                      </label>
                    </div>
                  )}
                  <label className="offer-check">
                    <input
                      type="checkbox"
                      checked={withFriends}
                      onChange={(e) => setWithFriends(e.target.checked)}
                    />
                    <span>{t("checkout.with_friends")}</span>
                  </label>
                  {withFriends && (
                    <div className="books-form">
                      <p className="aside">{t("checkout.with_friends_note")}</p>
                      <label>
                        <span>{t("checkout.group_days")}</span>
                        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
                          {GROUP_DAYS.map((count) => (
                            <option key={count} value={count}>
                              {t("checkout.group_days_option", { count })}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  )}
                </fieldset>
              )}
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
                    KNOWN_ERRORS.has(state)
                      ? (`checkout.err.${state}` as Key)
                      : "checkout.err.failed",
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
