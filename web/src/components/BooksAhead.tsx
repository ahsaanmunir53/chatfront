// The books ahead and behind: the cash-flow forecast (feature 58), the year with zakat (62),
// the workbook for tax filing (63) and the credit passport for a lender (64). Code works out
// every figure from the shop's own books; the owner gives the cash they counted and the
// figures zakat needs.
import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api";
import type { CashForecast, OwnerMe, Passport, YearSummary, Zakat } from "../api";
import { saveFile } from "../download";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney, parseAmount, toMinor } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 120_000;

function useMoney(me: OwnerMe) {
  const { lang } = useLang();
  return (minor: number) => formatMoney(minor, me.country, lang);
}

function dayLabel(iso: string, lang: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" });
}

function CashForm({
  me,
  token,
  onSaved,
}: {
  me: OwnerMe;
  token: string;
  onSaved: (found: CashForecast) => void;
}) {
  const { t } = useLang();
  const [amount, setAmount] = useState("");
  const [failed, setFailed] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault();
    const value = parseAmount(amount);
    if (value === null || value < 0) {
      setFailed(true);
      return;
    }
    setFailed(false);
    try {
      onSaved(await api.setCash(token, toMinor(value, me.country)));
      setAmount("");
    } catch {
      setFailed(true);
    }
  }
  return (
    <form className="books-form" onSubmit={(event) => void save(event)}>
      <label>
        <span>{t("console.cash_count")}</span>
        <input
          value={amount}
          required
          inputMode="decimal"
          maxLength={14}
          dir="ltr"
          onChange={(e) => setAmount(e.target.value)}
        />
      </label>
      <button type="submit" className="button button-ink button-small">
        {t("console.cash_save")}
      </button>
      {failed && <span role="alert">{t("console.offers_failed")}</span>}
    </form>
  );
}

function ForecastBlock({ me, token, canWrite }: { me: OwnerMe; token: string; canWrite: boolean }) {
  const { t, lang } = useLang();
  const money = useMoney(me);
  const load = useCallback(() => api.forecast(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [fresh, setFresh] = useState<CashForecast | null>(null);
  const found = fresh ?? data;
  if (!found) return null;
  return (
    <div className="pay-block">
      <h3 className="pay-block-title">{t("console.forecast_title")}</h3>
      <p className="aside">{t("console.forecast_lede")}</p>
      <p>
        {t("console.forecast_usual", {
          came: money(found.usual_in),
          went: money(found.usual_out),
        })}
      </p>
      {found.fixed.length > 0 && (
        <p className="pay-note">
          {found.fixed
            .map((f) =>
              t("console.forecast_fixed", {
                category: t(`books.category.${f.category}` as Key),
                amount: money(f.amount),
                day: f.day,
              }),
            )
            .join("; ")}
        </p>
      )}
      {found.cash_now === null ? (
        <p className="aside">{t("console.forecast_no_cash")}</p>
      ) : (
        <p>{t("console.forecast_cash_now", { amount: money(found.cash_now) })}</p>
      )}
      <table className="daybook forecast">
        <thead>
          <tr>
            <th scope="col">{t("console.forecast_week")}</th>
            <th scope="col">{t("console.forecast_in")}</th>
            <th scope="col">{t("console.forecast_out")}</th>
            <th scope="col">{t("console.forecast_balance")}</th>
          </tr>
        </thead>
        <tbody>
          {found.weeks.map((week) => (
            <tr key={week.starts} className={week.short ? "is-short" : undefined}>
              <th scope="row">{dayLabel(week.starts, lang)}</th>
              <td>{money(week.money_in)}</td>
              <td>{money(week.money_out)}</td>
              <td>
                {week.balance === null ? "-" : money(week.balance)}
                {week.short && <strong> {t("console.forecast_short")}</strong>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {canWrite && (
        <CashForm
          me={me}
          token={token}
          onSaved={(saved) => {
            setFresh(saved);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function ZakatForm({ me, token, year }: { me: OwnerMe; token: string; year: YearSummary }) {
  const { t } = useLang();
  const money = useMoney(me);
  const [fields, setFields] = useState({ cash: "", debts: "", nisab: "", stock: "", owed: "" });
  const [result, setResult] = useState<Zakat | null>(null);
  const [failed, setFailed] = useState(false);
  const minor = (text: string) => {
    const value = parseAmount(text);
    return value === null ? null : toMinor(value, me.country);
  };
  async function work(event: FormEvent) {
    event.preventDefault();
    setFailed(false);
    try {
      setResult(
        await api.zakat(token, {
          cash: minor(fields.cash) ?? 0,
          debts: minor(fields.debts) ?? 0,
          nisab: minor(fields.nisab) ?? 0,
          stock: fields.stock.trim() ? minor(fields.stock) : null,
          owed_to_shop: fields.owed.trim() ? minor(fields.owed) : null,
        }),
      );
    } catch {
      setFailed(true);
    }
  }
  const field = (name: keyof typeof fields, label: Key, hint?: string) => (
    <label>
      <span>{t(label)}</span>
      <input
        value={fields[name]}
        inputMode="decimal"
        maxLength={14}
        dir="ltr"
        placeholder={hint}
        onChange={(e) => setFields({ ...fields, [name]: e.target.value })}
      />
    </label>
  );
  return (
    <form className="books-form zakat" onSubmit={(event) => void work(event)}>
      <h3 className="pay-block-title">{t("console.zakat_title")}</h3>
      <p className="aside">{t("console.zakat_lede")}</p>
      {field("cash", "console.zakat_cash")}
      {field("stock", "console.zakat_stock", money(year.zakat_inputs.stock))}
      {year.zakat_inputs.stock_without_cost > 0 && (
        <p className="pay-note">
          {t("console.zakat_unknown_stock", { units: year.zakat_inputs.stock_without_cost })}
        </p>
      )}
      {field("owed", "console.zakat_owed", money(year.zakat_inputs.owed_to_shop))}
      {field("debts", "console.zakat_debts")}
      {field("nisab", "console.zakat_nisab")}
      <button type="submit" className="button button-ink button-small">
        {t("console.zakat_work")}
      </button>
      {failed && <span role="alert">{t("console.offers_failed")}</span>}
      {result && (
        <p role="status" className="zakat-result">
          {result.due > 0
            ? t("console.zakat_due", { net: money(result.net), due: money(result.due) })
            : t("console.zakat_none", { net: money(result.net), nisab: money(result.nisab) })}
        </p>
      )}
    </form>
  );
}

function YearBlock({ me, token }: { me: OwnerMe; token: string }) {
  const { t } = useLang();
  const money = useMoney(me);
  const load = useCallback(() => api.yearSummary(token), [token]);
  const { data } = usePolled(load, POLL_MS);
  if (!data) return null;
  return (
    <div className="pay-block">
      <h3 className="pay-block-title">{t("console.year_title", { year: data.label })}</h3>
      <ul className="pay-rows year-sums">
        <li>{t("console.year_sales", { amount: money(data.sales + data.udhaar_paid) })}</li>
        <li>{t("console.year_expenses", { amount: money(data.expenses + data.refunds) })}</li>
        <li>{t("console.year_cash_profit", { amount: money(data.cash_profit) })}</li>
        <li>{t("console.year_goods_profit", { amount: money(data.goods_profit) })}</li>
        <li>{t("console.year_orders", { count: data.orders })}</li>
      </ul>
      {data.top.length > 0 && (
        <p className="pay-note">
          {t("console.year_top", {
            names: data.top.map((s) => `${s.name} (${s.units})`).join(", "),
          })}
        </p>
      )}
      <ZakatForm me={me} token={token} year={data} />
    </div>
  );
}

function TaxWorkbook({ token }: { token: string }) {
  const { t } = useLang();
  const today = new Date().toISOString().slice(0, 10);
  const [start, setStart] = useState(`${today.slice(0, 4)}-01-01`);
  const [end, setEnd] = useState(today);
  const [failed, setFailed] = useState<string | null>(null);
  async function download(event: FormEvent) {
    event.preventDefault();
    setFailed(null);
    try {
      saveFile(await api.taxWorkbook(token, start, end), `books-${start}-${end}.xlsx`);
    } catch (error) {
      setFailed(error instanceof ApiError && error.code === "bad_range" ? "bad_range" : "failed");
    }
  }
  return (
    <form className="books-form" onSubmit={(event) => void download(event)}>
      <h3 className="pay-block-title">{t("console.tax_title")}</h3>
      <p className="aside">{t("console.tax_lede")}</p>
      <label>
        <span>{t("console.tax_from")}</span>
        <input type="date" value={start} required onChange={(e) => setStart(e.target.value)} />
      </label>
      <label>
        <span>{t("console.tax_to")}</span>
        <input type="date" value={end} required onChange={(e) => setEnd(e.target.value)} />
      </label>
      <button type="submit" className="button button-ink button-small">
        {t("console.tax_download")}
      </button>
      {failed && (
        <span role="alert">
          {t(failed === "bad_range" ? "console.err.bad_range" : "console.offers_failed")}
        </span>
      )}
    </form>
  );
}

function PassportBlock({ me, token, canMake }: { me: OwnerMe; token: string; canMake: boolean }) {
  const { t } = useLang();
  const money = useMoney(me);
  const load = useCallback(() => api.passports(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [agreed, setAgreed] = useState(false);
  const [failed, setFailed] = useState(false);
  async function make() {
    setFailed(false);
    try {
      await api.makePassport(token, t("console.passport_agree"));
      setAgreed(false);
      refresh();
    } catch {
      setFailed(true);
    }
  }
  const list: Passport[] = data ?? [];
  return (
    <div className="pay-block">
      <h3 className="pay-block-title">{t("console.passport_title")}</h3>
      <p className="aside">{t("console.passport_lede")}</p>
      {canMake && (
        <div className="passport-make">
          <label className="offer-check">
            <input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} />
            <span>{t("console.passport_agree")}</span>
          </label>
          <button
            type="button"
            className="button button-ink button-small"
            disabled={!agreed}
            onClick={() => void make()}
          >
            {t("console.passport_make")}
          </button>
          {failed && <span role="alert">{t("console.offers_failed")}</span>}
        </div>
      )}
      {list.length > 0 && (
        <ul className="pay-rows">
          {list.map((item) => (
            <li key={item.code}>
              <Link to={`/passport/${item.code}`} className="pay-code">
                {item.code}
              </Link>
              <span className="pay-note">{item.made_on}</span>
              <span className="pay-note">
                {t("console.passport_delivered", {
                  count: item.totals.orders_delivered ?? 0,
                  value: money(item.totals.delivered_value ?? 0),
                })}
              </span>
              {item.revoked ? (
                <span className="order-failed">{t("console.passport_revoked")}</span>
              ) : (
                canMake && (
                  <button
                    type="button"
                    className="action"
                    onClick={() => void api.revokePassport(token, item.code).then(refresh, refresh)}
                  >
                    {t("console.passport_revoke")}
                  </button>
                )
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function BooksAheadPanel({
  me,
  token,
  canWrite,
  canSettings,
}: {
  me: OwnerMe;
  token: string;
  canWrite: boolean;
  canSettings: boolean;
}) {
  const { t } = useLang();
  return (
    <section className="pay-panel books-ahead" aria-labelledby="books-ahead-title">
      <h2 id="books-ahead-title" className="section-title">
        {t("console.ahead_title")}
      </h2>
      <div className="pay-columns">
        <ForecastBlock me={me} token={token} canWrite={canWrite} />
        <YearBlock me={me} token={token} />
      </div>
      <div className="pay-columns">
        <div className="pay-block">
          <TaxWorkbook token={token} />
        </div>
        <PassportBlock me={me} token={token} canMake={canSettings} />
      </div>
    </section>
  );
}
