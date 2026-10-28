// Bringing customers back, in the offers panel: the reminder about a cart left in a chat
// (feature 14), the "send to a friend" programme (20), and what money sent from abroad is worth
// to the shop (96). Code sends the reminders, gives the codes and converts the money.
import { useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type { Offers, OwnerMe } from "../api";
import { useLang } from "../lang";
import { formatMoney, parseAmount, toMajor, toMinor } from "../money";
import { Status, useSave } from "./Saving";

const MAX_PERCENT = 50;

function percentOf(text: string, fallback: number): number {
  const value = Math.round(Number(text));
  return Number.isFinite(value) && value >= 1 ? Math.min(value, MAX_PERCENT) : fallback;
}

function Reminders({ me, token, data, onSaved }: Props) {
  const { t, lang } = useLang();
  const { state, run } = useSave(onSaved);
  const { on, reminded, placed, value } = data.reminders;
  return (
    <div className="books-form">
      <h3 className="pay-block-title">{t("console.reminders_title")}</h3>
      <p className="aside">{t("console.reminders_lede")}</p>
      <label className="offer-check">
        <input
          type="checkbox"
          checked={on}
          onChange={(e) => void run(() => api.setCartReminders(token, e.target.checked))}
        />
        <span>{t("console.reminders_on")}</span>
      </label>
      <p className="pay-note">
        {t("console.reminders_counts", {
          reminded,
          placed,
          value: formatMoney(value, me.country, lang),
        })}
      </p>
      <Status state={state} />
    </div>
  );
}

function Referral({ token, data, onSaved }: Props) {
  const { t } = useLang();
  const current = data.referral;
  const [on, setOn] = useState(current?.on ?? false);
  const [friend, setFriend] = useState(String(current?.friend_percent ?? 10));
  const [points, setPoints] = useState(String(current?.reward_points ?? 0));
  const [reward, setReward] = useState(String(current?.reward_percent ?? 10));
  const { state, run } = useSave(onSaved);
  const givesPoints = data.loyalty?.on ?? false;
  const stats = data.referral_stats;

  function save(event: FormEvent) {
    event.preventDefault();
    void run(() =>
      api.setReferral(token, {
        on,
        friend_percent: percentOf(friend, 10),
        reward_points: givesPoints ? Math.max(0, Math.round(Number(points) || 0)) : 0,
        reward_percent: percentOf(reward, 10),
      }),
    );
  }

  return (
    <form className="books-form" onSubmit={save}>
      <h3 className="pay-block-title">{t("console.referral_title")}</h3>
      <p className="aside">{t("console.referral_lede")}</p>
      <label className="offer-check">
        <input type="checkbox" checked={on} onChange={(e) => setOn(e.target.checked)} />
        <span>{t("console.referral_on")}</span>
      </label>
      <label>
        <span>{t("console.referral_friend")}</span>
        <input
          value={friend}
          inputMode="numeric"
          maxLength={2}
          dir="ltr"
          onChange={(e) => setFriend(e.target.value.replace(/\D/g, ""))}
        />
      </label>
      {givesPoints && (
        <label>
          <span>{t("console.referral_points")}</span>
          <input
            value={points}
            inputMode="numeric"
            maxLength={7}
            dir="ltr"
            onChange={(e) => setPoints(e.target.value.replace(/\D/g, ""))}
          />
        </label>
      )}
      <label>
        <span>{t("console.referral_reward")}</span>
        <input
          value={reward}
          inputMode="numeric"
          maxLength={2}
          dir="ltr"
          onChange={(e) => setReward(e.target.value.replace(/\D/g, ""))}
        />
      </label>
      <p className="pay-note">
        {t("console.referral_stats", {
          codes: stats.codes,
          friends: stats.friends,
          delivered: stats.delivered,
        })}
      </p>
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving"}
        >
          {t("console.referral_save")}
        </button>
        <Status state={state} />
      </div>
    </form>
  );
}

function Rates({ me, token, data, onSaved }: Props) {
  const { t, lang } = useLang();
  const [rates, setRates] = useState<Record<string, number>>(data.rates);
  const unused = data.currencies.filter((code) => !(code in rates));
  const [currency, setCurrency] = useState(unused[0] ?? "");
  const [worth, setWorth] = useState("");
  const { state, run } = useSave(onSaved);

  function save(next: Record<string, number>) {
    setRates(next);
    void run(() => api.setRates(token, next));
  }

  function add(event: FormEvent) {
    event.preventDefault();
    const amount = parseAmount(worth);
    const chosen = currency || unused[0];
    if (!chosen || amount === null || amount <= 0) return;
    save({ ...rates, [chosen]: toMinor(amount, me.country) });
    setWorth("");
    setCurrency("");
  }

  return (
    <form className="books-form" onSubmit={add}>
      <h3 className="pay-block-title">{t("console.rates_title")}</h3>
      <p className="aside">{t("console.rates_lede")}</p>
      {Object.keys(rates).length > 0 && (
        <ul className="pay-rows">
          {Object.entries(rates).map(([code, minor]) => (
            <li key={code}>
              <span className="pay-code">{code}</span>
              <span className="pay-amount">
                {t("console.rate_line", { code, worth: formatMoney(minor, me.country, lang) })}
              </span>
              <button
                type="button"
                className="action offer-stop"
                onClick={() =>
                  save(Object.fromEntries(Object.entries(rates).filter(([c]) => c !== code)))
                }
              >
                {t("console.rate_remove")}
              </button>
            </li>
          ))}
        </ul>
      )}
      {unused.length > 0 && (
        <>
          <label>
            <span>{t("console.rate_currency")}</span>
            <select value={currency || unused[0]} onChange={(e) => setCurrency(e.target.value)}>
              {unused.map((code) => (
                <option key={code} value={code}>
                  {code}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{t("console.rate_worth", { currency: data.currency })}</span>
            <input
              value={worth}
              inputMode="decimal"
              maxLength={10}
              dir="ltr"
              placeholder={String(toMajor(28_000, me.country))}
              onChange={(e) => setWorth(e.target.value)}
            />
          </label>
        </>
      )}
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving" || unused.length === 0}
        >
          {t("console.rate_add")}
        </button>
        <Status state={state} />
      </div>
    </form>
  );
}

type Props = { me: OwnerMe; token: string; data: Offers; onSaved: () => void };

/** The fourth column of the offers panel. */
export function OfferExtras(props: Props) {
  return (
    <div className="pay-block">
      <Reminders {...props} />
      <Referral {...props} />
      <Rates {...props} />
    </div>
  );
}
