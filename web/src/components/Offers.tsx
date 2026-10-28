// Offers in the console: discount codes and sales (features 19 and 95), bundles sold together
// for less (feature 13), loyalty points (feature 19) and the shop's own big days, which the shop
// page counts down to (feature 95). Code applies all of them; nothing here is a model's.
import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type {
  CouponIn,
  LoyaltySettings,
  OwnerBundle,
  OwnerCoupon,
  OwnerMe,
  OwnerProduct,
  ShopEvent,
} from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney, parseAmount, toMinor } from "../money";
import { dayLabel } from "../pages/ShopPage";
import { usePolled } from "../usePolled";

const POLL_MS = 30_000;
type Saving = "idle" | "saving" | "saved" | "failed";

function Status({ state }: { state: Saving }) {
  const { t } = useLang();
  if (state === "saved") return <span role="status">{t("console.saved")}</span>;
  if (state === "failed") return <span role="alert">{t("console.offers_failed")}</span>;
  return null;
}

/** Runs a save and keeps its state for the form's status line. */
function useSave(onDone: () => void) {
  const [state, setState] = useState<Saving>("idle");
  const run = useCallback(
    async (step: () => Promise<unknown>) => {
      setState("saving");
      try {
        await step();
        setState("saved");
        onDone();
        return true;
      } catch {
        setState("failed");
        return false;
      }
    },
    [onDone],
  );
  return { state, run };
}

function CouponRow({
  me,
  token,
  coupon,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  coupon: OwnerCoupon;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  const money = (minor: number) => formatMoney(minor, me.country, lang);
  return (
    <li className={coupon.active ? undefined : "is-removed"}>
      <span className="pay-code">{coupon.code}</span>
      <span className="pay-amount">
        {coupon.percent !== null
          ? t("console.coupon_percent_off", { percent: coupon.percent })
          : t("console.coupon_amount_off", { amount: money(coupon.amount ?? 0) })}
      </span>
      {coupon.min_subtotal > 0 && (
        <span className="pay-note">
          {t("console.coupon_min_note", { amount: money(coupon.min_subtotal) })}
        </span>
      )}
      {coupon.ends_on && (
        <span className="pay-note">
          {t("console.coupon_until", { date: dayLabel(coupon.ends_on, lang) })}
        </span>
      )}
      <span className="pay-note">{t("console.coupon_uses", { count: coupon.uses })}</span>
      {coupon.show_on_page && <span className="pay-note">{t("console.coupon_sale")}</span>}
      {coupon.active ? (
        <button
          type="button"
          className="action offer-stop"
          onClick={() => void api.switchOff(token, "coupons", coupon.id).then(onChange, onChange)}
        >
          {t("console.offer_stop")}
        </button>
      ) : (
        <span className="pay-note">{t("console.offer_stopped")}</span>
      )}
    </li>
  );
}

function CouponForm({ me, token, onSaved }: { me: OwnerMe; token: string; onSaved: () => void }) {
  const { t } = useLang();
  const [code, setCode] = useState("");
  const [kind, setKind] = useState<"percent" | "amount">("percent");
  const [value, setValue] = useState("");
  const [least, setLeast] = useState("");
  const [ends, setEnds] = useState("");
  const [most, setMost] = useState("");
  const [sale, setSale] = useState(false);
  const { state, run } = useSave(onSaved);

  async function save(event: FormEvent) {
    event.preventDefault();
    const number = parseAmount(value);
    if (number === null) return;
    const coupon: CouponIn = {
      code: code.trim().toUpperCase(),
      percent: kind === "percent" ? Math.round(number) : null,
      amount: kind === "amount" ? toMinor(number, me.country) : null,
      min_subtotal: toMinor(parseAmount(least) ?? 0, me.country),
      starts_on: null,
      ends_on: ends || null,
      max_uses: most ? Math.round(Number(most)) : null,
      once_per_customer: true,
      show_on_page: sale,
    };
    if (await run(() => api.addCoupon(token, coupon))) {
      setCode("");
      setValue("");
    }
  }

  return (
    <form className="books-form" onSubmit={(event) => void save(event)}>
      <h3 className="pay-block-title">{t("console.coupon_add_title")}</h3>
      <label>
        <span>{t("console.coupon_code")}</span>
        <input
          value={code}
          required
          minLength={2}
          maxLength={24}
          dir="ltr"
          pattern="[A-Za-z0-9]+"
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      <div className="khata-kinds" role="radiogroup" aria-label={t("console.coupon_kind")}>
        {(["percent", "amount"] as const).map((option) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={kind === option}
            onClick={() => setKind(option)}
          >
            {t(`console.coupon_kind_${option}` as Key)}
          </button>
        ))}
      </div>
      <label>
        <span>{kind === "percent" ? t("console.coupon_percent") : t("console.coupon_amount")}</span>
        <input
          value={value}
          required
          inputMode="decimal"
          maxLength={14}
          dir="ltr"
          onChange={(e) => setValue(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.coupon_min")}</span>
        <input
          value={least}
          inputMode="decimal"
          maxLength={14}
          dir="ltr"
          onChange={(e) => setLeast(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.coupon_ends")}</span>
        <input type="date" value={ends} onChange={(e) => setEnds(e.target.value)} />
      </label>
      <label>
        <span>{t("console.coupon_max")}</span>
        <input
          value={most}
          inputMode="numeric"
          maxLength={7}
          dir="ltr"
          onChange={(e) => setMost(e.target.value.replace(/\D/g, ""))}
        />
      </label>
      <label className="offer-check">
        <input type="checkbox" checked={sale} onChange={(e) => setSale(e.target.checked)} />
        <span>{t("console.coupon_show")}</span>
      </label>
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving"}
        >
          {t("console.coupon_add")}
        </button>
        <Status state={state} />
      </div>
    </form>
  );
}

function BundleRow({
  me,
  token,
  bundle,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  bundle: OwnerBundle;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  return (
    <li className={bundle.active ? undefined : "is-removed"}>
      <strong dir="auto">{bundle.name}</strong>
      <span className="pay-note" dir="auto">
        {bundle.products.map((p) => p.name).join(", ")}
      </span>
      <span className="pay-amount">
        {t("console.bundle_saving", { amount: formatMoney(bundle.off, me.country, lang) })}
      </span>
      {bundle.active ? (
        <button
          type="button"
          className="action offer-stop"
          onClick={() => void api.switchOff(token, "bundles", bundle.id).then(onChange, onChange)}
        >
          {t("console.offer_stop")}
        </button>
      ) : (
        <span className="pay-note">{t("console.offer_stopped")}</span>
      )}
    </li>
  );
}

function BundleForm({
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
  const { t } = useLang();
  const [name, setName] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [off, setOff] = useState("");
  const { state, run } = useSave(onSaved);
  const onSale = products.filter((p) => p.status === "active");

  function toggle(code: string) {
    setCodes((now) =>
      now.includes(code) ? now.filter((c) => c !== code) : [...now, code].slice(0, 4),
    );
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const major = parseAmount(off);
    if (major === null || codes.length < 2) return;
    const saved = await run(() =>
      api.addBundle(token, { name, product_codes: codes, off: toMinor(major, me.country) }),
    );
    if (saved) {
      setName("");
      setCodes([]);
      setOff("");
    }
  }

  return (
    <form className="books-form" onSubmit={(event) => void save(event)}>
      <h3 className="pay-block-title">{t("console.bundle_add_title")}</h3>
      <label>
        <span>{t("console.bundle_name")}</span>
        <input
          value={name}
          required
          minLength={2}
          maxLength={80}
          dir="auto"
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <fieldset className="offer-products">
        <legend>{t("console.bundle_products")}</legend>
        {onSale.map((product) => (
          <label key={product.code} className="offer-check">
            <input
              type="checkbox"
              checked={codes.includes(product.code)}
              disabled={!codes.includes(product.code) && codes.length >= 4}
              onChange={() => toggle(product.code)}
            />
            <span dir="auto">{product.name}</span>
          </label>
        ))}
      </fieldset>
      <label>
        <span>{t("console.bundle_off")}</span>
        <input
          value={off}
          required
          inputMode="decimal"
          maxLength={14}
          dir="ltr"
          onChange={(e) => setOff(e.target.value)}
        />
      </label>
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving" || codes.length < 2}
        >
          {t("console.bundle_add")}
        </button>
        <Status state={state} />
      </div>
    </form>
  );
}

function LoyaltyForm({
  me,
  token,
  current,
  onSaved,
}: {
  me: OwnerMe;
  token: string;
  current: LoyaltySettings | null;
  onSaved: () => void;
}) {
  const { t, lang } = useLang();
  const scale = toMinor(1, me.country);
  const [on, setOn] = useState(current !== null);
  const [earn, setEarn] = useState(current ? String(current.earn_per / scale) : "100");
  const [value, setValue] = useState(current ? String(current.point_value / scale) : "1");
  const [share, setShare] = useState(current ? String(current.max_share) : "20");
  const { state, run } = useSave(onSaved);
  const earnMinor = toMinor(parseAmount(earn) ?? 0, me.country);
  const valueMinor = toMinor(parseAmount(value) ?? 0, me.country);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (earnMinor <= 0 || valueMinor <= 0) return;
    const settings = {
      on,
      earn_per: earnMinor,
      point_value: valueMinor,
      max_share: Math.min(Math.max(Math.round(Number(share) || 20), 1), 100),
    };
    await run(() => api.setLoyalty(token, settings));
  }

  return (
    <form className="books-form" onSubmit={(event) => void save(event)}>
      <h3 className="pay-block-title">{t("console.loyalty_title")}</h3>
      <label className="offer-check">
        <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />
        <span>{t("console.loyalty_on")}</span>
      </label>
      <label>
        <span>{t("console.loyalty_earn")}</span>
        <input
          value={earn}
          inputMode="decimal"
          maxLength={14}
          dir="ltr"
          onChange={(e) => setEarn(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.loyalty_value")}</span>
        <input
          value={value}
          inputMode="decimal"
          maxLength={14}
          dir="ltr"
          onChange={(e) => setValue(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.loyalty_share")}</span>
        <input
          value={share}
          inputMode="numeric"
          maxLength={3}
          dir="ltr"
          onChange={(e) => setShare(e.target.value.replace(/\D/g, ""))}
        />
      </label>
      {on && earnMinor > 0 && valueMinor > 0 && (
        <p className="aside">
          {t("console.loyalty_note", {
            earn: formatMoney(earnMinor, me.country, lang),
            value: formatMoney(valueMinor, me.country, lang),
            share: Number(share) || 20,
          })}
        </p>
      )}
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving"}
        >
          {t("console.loyalty_save")}
        </button>
        <Status state={state} />
      </div>
    </form>
  );
}

function EventsForm({
  token,
  current,
  onSaved,
}: {
  token: string;
  current: ShopEvent[];
  onSaved: () => void;
}) {
  const { t, lang } = useLang();
  const [events, setEvents] = useState<ShopEvent[]>(current);
  const [name, setName] = useState("");
  const [date, setDate] = useState("");
  const { state, run } = useSave(onSaved);

  function save(next: ShopEvent[]) {
    setEvents(next);
    void run(() => api.setEvents(token, next));
  }

  function add(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !date) return;
    save([...events, { name: name.trim(), date }].sort((a, b) => a.date.localeCompare(b.date)));
    setName("");
    setDate("");
  }

  return (
    <form className="books-form" onSubmit={add}>
      <h3 className="pay-block-title">{t("console.events_title")}</h3>
      <p className="aside">{t("console.events_lede")}</p>
      {events.length > 0 && (
        <ul className="pay-rows">
          {events.map((item) => (
            <li key={`${item.name}-${item.date}`}>
              <strong dir="auto">{item.name}</strong>
              <span className="pay-note">{dayLabel(item.date, lang)}</span>
              <button
                type="button"
                className="action offer-stop"
                onClick={() => save(events.filter((e) => e !== item))}
              >
                {t("console.event_remove")}
              </button>
            </li>
          ))}
        </ul>
      )}
      <label>
        <span>{t("console.event_name")}</span>
        <input value={name} maxLength={60} dir="auto" onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        <span>{t("console.event_date")}</span>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving"}
        >
          {t("console.event_add")}
        </button>
        <Status state={state} />
      </div>
    </form>
  );
}

/** Codes, bundles, points and big days, for people who set the shop's prices. */
export function OffersPanel({
  me,
  token,
  products,
}: {
  me: OwnerMe;
  token: string;
  products: OwnerProduct[];
}) {
  const { t } = useLang();
  const load = useCallback(() => api.offers(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  if (!data) return null;
  return (
    <section className="pay-panel offers" aria-labelledby="offers-title">
      <h2 id="offers-title" className="section-title">
        {t("console.offers_title")}
      </h2>
      <p className="aside">{t("console.offers_lede")}</p>
      <div className="pay-columns offer-columns">
        <div className="pay-block">
          <h3 id="coupons-title" className="pay-block-title">
            {t("console.coupons_title")}
          </h3>
          {data.coupons.length === 0 ? (
            <p className="aside">{t("console.coupons_none")}</p>
          ) : (
            <ul className="pay-rows" aria-labelledby="coupons-title">
              {data.coupons.map((coupon) => (
                <CouponRow
                  key={coupon.id}
                  me={me}
                  token={token}
                  coupon={coupon}
                  onChange={refresh}
                />
              ))}
            </ul>
          )}
          <CouponForm me={me} token={token} onSaved={refresh} />
        </div>
        <div className="pay-block">
          <h3 id="bundles-title" className="pay-block-title">
            {t("console.bundles_title")}
          </h3>
          {data.bundles.length === 0 ? (
            <p className="aside">{t("console.bundles_none")}</p>
          ) : (
            <ul className="pay-rows" aria-labelledby="bundles-title">
              {data.bundles.map((bundle) => (
                <BundleRow
                  key={bundle.id}
                  me={me}
                  token={token}
                  bundle={bundle}
                  onChange={refresh}
                />
              ))}
            </ul>
          )}
          <BundleForm me={me} token={token} products={products} onSaved={refresh} />
        </div>
        <div className="pay-block">
          <LoyaltyForm me={me} token={token} current={data.loyalty} onSaved={refresh} />
          <EventsForm token={token} current={data.events} onSaved={refresh} />
        </div>
      </div>
    </section>
  );
}
