// The books in the console (features 48, 49, 50, 52, 119): a day book of today, this week and
// this month with cash profit and goods profit, the latest entries with Undo, an expense form,
// the udhaar khata with its own form, and what is running low. Every figure comes from the
// API; nothing is added up here.
import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type { Books, KhataAccount, LedgerEntry, LowStock, OwnerMe, Sums } from "../api";
import { isolate } from "../i18n";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";
import { formatMoney, parseAmount, toMinor } from "../money";

const POLL_MS = 8000;
const PERIODS = ["today", "week", "month"] as const;
// The day book's rows: what came in, what went out, and what is left.
const ROWS: { key: Key; pick: (sums: Sums) => number }[] = [
  { key: "console.books_sales", pick: (s) => s.sales },
  { key: "console.books_udhaar_paid", pick: (s) => s.udhaar_paid },
  { key: "console.books_expenses", pick: (s) => s.expenses },
  { key: "console.books_udhaar_given", pick: (s) => s.udhaar_given },
  { key: "console.books_write_offs", pick: (s) => s.write_offs },
];
const IN_KINDS = new Set(["sale", "udhaar_paid"]);

/** Polls the books (for people who keep them) and what is running low; refresh() after a
 * change. */
export function useBooks(token: string, canRead: boolean) {
  const loadBooks = useCallback(() => api.books(token), [token]);
  const loadLow = useCallback(() => api.lowStock(token), [token]);
  const books = usePolled(canRead ? loadBooks : null, POLL_MS);
  const low = usePolled(loadLow, POLL_MS);
  const refresh = useCallback(() => {
    books.refresh();
    low.refresh();
  }, [books, low]);
  return { books: books.data, low: low.data ?? [], refresh };
}

function DayBook({ me, books }: { me: OwnerMe; books: Books }) {
  const { t, lang } = useLang();
  const money = (minor: number) => formatMoney(minor, me.country, lang);
  return (
    <table className="daybook">
      <thead>
        <tr>
          <td />
          {PERIODS.map((period) => (
            <th key={period} scope="col">
              {t(`console.books_${period}` as Key)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {ROWS.map((row) => (
          <tr key={row.key}>
            <th scope="row">{t(row.key)}</th>
            {PERIODS.map((period) => (
              <td key={period}>{money(row.pick(books[period]))}</td>
            ))}
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <th scope="row">{t("console.books_profit")}</th>
          {PERIODS.map((period) => (
            <td key={period} className={books[period].cash_profit < 0 ? "is-loss" : undefined}>
              {money(books[period].cash_profit)}
            </td>
          ))}
        </tr>
        <tr className="is-goods">
          <th scope="row">{t("console.books_goods_profit")}</th>
          {PERIODS.map((period) => (
            <td key={period} className={books[period].goods_profit < 0 ? "is-loss" : undefined}>
              {money(books[period].goods_profit)}
            </td>
          ))}
        </tr>
      </tfoot>
    </table>
  );
}

function GoodsNote({ books }: { books: Books }) {
  const { t } = useLang();
  const unknown = books.month.cost_unknown;
  return (
    <p className="pay-note daybook-note">
      {t("console.books_goods_note")}
      {unknown > 0 && ` ${t("console.profit_unknown", { count: unknown })}.`}
    </p>
  );
}

function Entry({
  me,
  entry,
  canUndo,
  onUndo,
}: {
  me: OwnerMe;
  entry: LedgerEntry;
  canUndo: boolean;
  onUndo: (entry: LedgerEntry) => void;
}) {
  const { t, lang } = useLang();
  const incoming = IN_KINDS.has(entry.kind) === entry.amount > 0;
  const what = entry.order_code
    ? t("receipt.order", { code: isolate(entry.order_code) })
    : entry.account
      ? entry.account
      : t(`books.category.${entry.category}` as Key);
  const undoable =
    canUndo &&
    !entry.reversed &&
    entry.reverses === null &&
    !entry.kind.startsWith("udhaar") &&
    entry.kind !== "write_off";
  return (
    <li className={`entry-line${entry.reversed || entry.reverses ? " is-undone" : ""}`}>
      <time dateTime={entry.occurred_on}>
        {new Date(`${entry.occurred_on}T12:00:00`).toLocaleDateString(lang, {
          day: "numeric",
          month: "short",
        })}
      </time>
      <span className="entry-what">
        <span className="entry-kind">{t(`console.entry.${entry.kind}` as Key)}</span>
        <span dir="auto">{what}</span>
        {entry.description && entry.description !== entry.account && (
          <span className="entry-note" dir="auto">
            {entry.description}
          </span>
        )}
        {entry.has_bill && <span className="entry-bill">{t("console.books_bill")}</span>}
      </span>
      <span className={`entry-amount ${incoming ? "is-in" : "is-out"}`}>
        {incoming ? "+" : "−"}
        {formatMoney(Math.abs(entry.amount), me.country, lang)}
      </span>
      {undoable && (
        <button type="button" className="entry-undo" onClick={() => onUndo(entry)}>
          {t("console.books_undo")}
        </button>
      )}
    </li>
  );
}

function ExpenseForm({
  me,
  token,
  categories,
  onSaved,
}: {
  me: OwnerMe;
  token: string;
  categories: string[];
  onSaved: () => void;
}) {
  const { t } = useLang();
  const [amount, setAmount] = useState("");
  const [category, setCategory] = useState(categories[0] ?? "other");
  const [description, setDescription] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");

  async function save(event: FormEvent) {
    event.preventDefault();
    const major = parseAmount(amount);
    if (major === null) {
      setState("failed");
      return;
    }
    setState("saving");
    try {
      await api.addExpense(token, { amount: toMinor(major, me.country), category, description });
      setAmount("");
      setDescription("");
      setState("saved");
      onSaved();
    } catch {
      setState("failed");
    }
  }

  return (
    <form className="books-form" onSubmit={(event) => void save(event)}>
      <h3 className="pay-block-title">{t("console.books_add_expense")}</h3>
      <label>
        <span>{t("console.books_amount")}</span>
        <input
          value={amount}
          inputMode="decimal"
          required
          maxLength={14}
          dir="ltr"
          onChange={(e) => setAmount(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.books_category")}</span>
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {categories.map((name) => (
            <option key={name} value={name}>
              {t(`books.category.${name}` as Key)}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{t("console.books_note")}</span>
        <input
          value={description}
          maxLength={200}
          dir="auto"
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving"}
        >
          {t("console.books_save_expense")}
        </button>
        {state === "saved" && <span role="status">{t("console.saved")}</span>}
        {state === "failed" && <span role="alert">{t("console.save_failed")}</span>}
      </div>
    </form>
  );
}

function Khata({
  me,
  token,
  accounts,
  canWrite,
  onSaved,
}: {
  me: OwnerMe;
  token: string;
  accounts: KhataAccount[];
  canWrite: boolean;
  onSaved: () => void;
}) {
  const { t, lang } = useLang();
  const [kind, setKind] = useState<"udhaar_given" | "udhaar_paid">("udhaar_given");
  const [who, setWho] = useState("");
  const [amount, setAmount] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const money = (minor: number) => formatMoney(minor, me.country, lang);

  async function save(event: FormEvent) {
    event.preventDefault();
    const major = parseAmount(amount);
    const account = accounts.find((a) => a.name.toLowerCase() === who.trim().toLowerCase());
    if (major === null || (kind === "udhaar_paid" && !account)) {
      setState("failed");
      return;
    }
    setState("saving");
    try {
      await api.moveUdhaar(token, {
        kind,
        amount: toMinor(major, me.country),
        ...(account ? { account_id: account.id } : { name: who.trim() }),
      });
      setAmount("");
      setState("saved");
      onSaved();
    } catch {
      setState("failed");
    }
  }

  return (
    <div className="pay-block khata-block">
      <h3 className="pay-block-title">
        {t("console.khata_title")}
        <span className="console-count">{accounts.length}</span>
      </h3>
      {accounts.length === 0 ? (
        <p className="aside">{t("console.khata_none")}</p>
      ) : (
        <ul className="pay-rows">
          {accounts.map((account) => (
            <li key={account.id}>
              <span dir="auto" className="khata-who">
                {account.name}
              </span>
              <span className="pay-amount">{money(account.balance)}</span>
              {account.owed_days ? (
                <span className="pay-note">
                  {t("console.khata_days", { days: account.owed_days })}
                </span>
              ) : null}
              {account.reminders_sent > 0 && (
                <span className="pay-note">
                  {t("console.khata_reminders", { count: account.reminders_sent })}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {canWrite && (
        <form className="books-form" onSubmit={(event) => void save(event)}>
          <div className="khata-kinds" role="radiogroup" aria-label={t("console.khata_title")}>
            {(["udhaar_given", "udhaar_paid"] as const).map((option) => (
              <button
                key={option}
                type="button"
                role="radio"
                aria-checked={kind === option}
                onClick={() => setKind(option)}
              >
                {t(`console.entry.${option}` as Key)}
              </button>
            ))}
          </div>
          <label>
            <span>{t("console.name")}</span>
            <input
              value={who}
              list="khata-names"
              required
              maxLength={120}
              dir="auto"
              onChange={(e) => setWho(e.target.value)}
            />
            <datalist id="khata-names">
              {accounts.map((account) => (
                <option key={account.id} value={account.name} />
              ))}
            </datalist>
          </label>
          <label>
            <span>{t("console.books_amount")}</span>
            <input
              value={amount}
              inputMode="decimal"
              required
              maxLength={14}
              dir="ltr"
              onChange={(e) => setAmount(e.target.value)}
            />
          </label>
          <div className="wording-actions">
            <button
              type="submit"
              className="button button-ink button-small"
              disabled={state === "saving"}
            >
              {t("console.save")}
            </button>
            {state === "saved" && <span role="status">{t("console.saved")}</span>}
            {state === "failed" && <span role="alert">{t("console.save_failed")}</span>}
          </div>
        </form>
      )}
    </div>
  );
}

export function LowStockList({ low }: { low: LowStock[] }) {
  const { t } = useLang();
  if (low.length === 0) return null;
  return (
    <div className="low-stock" role="status">
      <strong>{t("console.low_title")}</strong>
      <ul>
        {low.map((item) => (
          <li key={item.name}>
            <span dir="auto">{item.name}</span>
            <span>{t("console.low_line", { left: item.left, limit: item.limit })}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function BooksPanel({
  me,
  token,
  books,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  books: Books | null;
  onChange: () => void;
}) {
  const { t } = useLang();
  const [failed, setFailed] = useState(false);
  const canWrite = me.rights.includes("books_write");
  if (!books) return null;

  async function undo(entry: LedgerEntry) {
    setFailed(false);
    try {
      await api.reverseEntry(token, entry.id);
      onChange();
    } catch {
      setFailed(true);
    }
  }

  return (
    <section className="books-panel" aria-labelledby="books-panel-title">
      <h2 id="books-panel-title" className="section-title">
        {t("console.books_title")}
      </h2>
      <p className="aside">{t("console.books_lede")}</p>
      <DayBook me={me} books={books} />
      <GoodsNote books={books} />
      <div className="pay-columns books-columns">
        <div className="pay-block">
          <h3 className="pay-block-title">{t("console.books_entries")}</h3>
          {books.entries.length === 0 ? (
            <p className="aside">{t("console.books_none")}</p>
          ) : (
            <ul className="books-entries">
              {books.entries.map((entry) => (
                <Entry
                  key={entry.id}
                  me={me}
                  entry={entry}
                  canUndo={canWrite}
                  onUndo={(e) => void undo(e)}
                />
              ))}
            </ul>
          )}
          {failed && (
            <p className="order-failed" role="alert">
              {t("console.move_failed")}
            </p>
          )}
        </div>
        <Khata
          me={me}
          token={token}
          accounts={books.accounts}
          canWrite={canWrite}
          onSaved={onChange}
        />
        {canWrite && (
          <div className="pay-block">
            <ExpenseForm me={me} token={token} categories={books.categories} onSaved={onChange} />
          </div>
        )}
      </div>
    </section>
  );
}
