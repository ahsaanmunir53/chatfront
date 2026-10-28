// Staff pay (feature 60): each person's monthly salary, a commission on the orders their
// chats brought that were delivered in the month, and the day it is paid. Code works out the
// month's pay; Paid puts it in the books once.
import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, api } from "../api";
import type { OwnerMe, StaffPayRow } from "../api";
import { useLang } from "../lang";
import { formatMoney, parseAmount, toMajor, toMinor } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 60_000;

function thisMonth(): string {
  return new Date().toISOString().slice(0, 7);
}

function PayForm({
  me,
  token,
  row,
  onSaved,
}: {
  me: OwnerMe;
  token: string;
  row: StaffPayRow;
  onSaved: () => void;
}) {
  const { t } = useLang();
  const [salary, setSalary] = useState(String(toMajor(row.salary, me.country)));
  const [percent, setPercent] = useState(String(row.commission_bp / 100));
  const [day, setDay] = useState(String(row.pay_day));
  const [failed, setFailed] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault();
    const amount = parseAmount(salary);
    const rate = Number(percent);
    if (amount === null || amount < 0 || !(rate >= 0 && rate <= 50)) {
      setFailed(true);
      return;
    }
    setFailed(false);
    try {
      await api.setStaffPay(token, row.staff_id, {
        salary: toMinor(amount, me.country),
        commission_bp: Math.round(rate * 100),
        pay_day: Math.min(28, Math.max(1, Math.round(Number(day) || 1))),
      });
      onSaved();
    } catch {
      setFailed(true);
    }
  }
  return (
    <form className="pay-form" onSubmit={(event) => void save(event)}>
      <label>
        <span>{t("console.pay_salary")}</span>
        <input
          value={salary}
          inputMode="decimal"
          maxLength={12}
          dir="ltr"
          onChange={(e) => setSalary(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.pay_commission")}</span>
        <input
          value={percent}
          inputMode="decimal"
          maxLength={5}
          dir="ltr"
          onChange={(e) => setPercent(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.pay_day")}</span>
        <input
          value={day}
          inputMode="numeric"
          maxLength={2}
          dir="ltr"
          onChange={(e) => setDay(e.target.value.replace(/\D/g, ""))}
        />
      </label>
      <button type="submit" className="action">
        {t("console.pay_save")}
      </button>
      {failed && <span role="alert">{t("console.pay_bad")}</span>}
    </form>
  );
}

function PayRow({
  me,
  token,
  row,
  month,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  row: StaffPayRow;
  month: string;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  const money = (minor: number) => formatMoney(minor, me.country, lang);
  const [failed, setFailed] = useState<string | null>(null);
  async function pay() {
    setFailed(null);
    try {
      await api.payStaff(token, row.staff_id, month);
      onChange();
    } catch (error) {
      setFailed(error instanceof ApiError ? error.code : "failed");
    }
  }
  return (
    <li className="staff-pay-row">
      <p className="waiting-head">
        <strong dir="auto">{row.name}</strong>
        <span className="pay-note">
          {t("console.pay_sales", { sales: money(row.sales), commission: money(row.commission) })}
        </span>
        <span className="pay-amount">{t("console.pay_due", { amount: money(row.due) })}</span>
        {row.paid !== null ? (
          <span className="pay-on">{t("console.pay_paid", { amount: money(row.paid) })}</span>
        ) : (
          row.due > 0 && (
            <button type="button" className="action action-primary" onClick={() => void pay()}>
              {t("console.pay_mark")}
            </button>
          )
        )}
      </p>
      <PayForm me={me} token={token} row={row} onSaved={onChange} />
      {failed && <p role="alert">{t("console.offers_failed")}</p>}
    </li>
  );
}

export function StaffPayPanel({ me, token }: { me: OwnerMe; token: string }) {
  const { t } = useLang();
  const [month, setMonth] = useState(thisMonth());
  const load = useCallback(() => api.staffPay(token, month), [token, month]);
  const { data, refresh } = usePolled(load, POLL_MS);
  if (!data) return null;
  return (
    <section className="pay-panel staff-pay" aria-labelledby="staff-pay-title">
      <h2 id="staff-pay-title" className="section-title">
        {t("console.pay_title")}
      </h2>
      <p className="aside">{t("console.pay_lede")}</p>
      <label className="pay-month">
        <span>{t("console.pay_month")}</span>
        <input
          type="month"
          value={month}
          onChange={(e) => e.target.value && setMonth(e.target.value)}
        />
      </label>
      <ul className="pay-rows">
        {data.map((row) => (
          <PayRow
            key={`${row.staff_id}-${month}`}
            me={me}
            token={token}
            row={row}
            month={month}
            onChange={refresh}
          />
        ))}
      </ul>
    </section>
  );
}
