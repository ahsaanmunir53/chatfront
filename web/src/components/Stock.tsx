// Stock in the console (features 117 to 120): stock that came in, by form or from a supplier's
// sheet; each batch with what is left of it, its cost, its expiry date and, for people who keep
// the books, its profit; discards; and profit per product. The alerts strip carries low stock,
// expiry dates and price questions to everyone whose role may answer them. Every figure comes
// from the API; nothing is worked out here.
import { useCallback, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import { ApiError, api } from "../api";
import type {
  Batch,
  DiscardReason,
  OwnerMe,
  OwnerNotice,
  OwnerProduct,
  ProductProfit,
  RestockLine,
  SheetReading,
} from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney, parseAmount, toMinor } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 8000;
const BATCHES_SHOWN = 12;
const REASONS: DiscardReason[] = ["expired", "damaged", "lost"];
const REORDER_LINK = /(https:\/\/wa\.me\/\S+)/;
const PRICE_BUTTON = /^pq:([^:]+):(now|later)$/;

type Choice = { id: string; label: string };

/** Every variant as one line to pick, "Printed lawn 3-piece (Rust)". */
function variantChoices(products: OwnerProduct[] | null): Choice[] {
  return (products ?? []).flatMap((product) =>
    product.variants.map((variant) => {
      const options = Object.values(variant.options).join(" ");
      return {
        id: variant.id,
        label: options ? `${product.name} (${options})` : product.name,
      };
    }),
  );
}

function wholeNumber(typed: string): number | null {
  const amount = parseAmount(typed);
  return amount !== null && Number.isInteger(amount) ? amount : null;
}

/** A reply with the supplier's wa.me link made clickable. */
function WithLink({ text }: { text: string }) {
  const parts = text.split(REORDER_LINK);
  return (
    <>
      {parts.map((part, index) =>
        REORDER_LINK.test(part) ? (
          <a key={index} href={part} target="_blank" rel="noreferrer" dir="ltr">
            {part}
          </a>
        ) : (
          <span key={index}>{part}</span>
        ),
      )}
    </>
  );
}

// -- alerts ----------------------------------------------------------------------------------

/** Polls the notices that wait for this shop; refresh() after an answer. */
export function useNotices(token: string) {
  const load = useCallback(() => api.notices(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  return { list: data ?? [], refresh };
}

type Answered = { notice: OwnerNotice; replies: string[] };

function PriceForm({
  me,
  token,
  batchId,
  choice,
  onDone,
}: {
  me: OwnerMe;
  token: string;
  batchId: string;
  choice: "now" | "later";
  onDone: (replies: string[]) => void;
}) {
  const { t, lang } = useLang();
  const [price, setPrice] = useState("");
  const [failed, setFailed] = useState(false);
  const money = (minor: number) => formatMoney(minor, me.country, lang);

  async function save(event: FormEvent) {
    event.preventDefault();
    const major = parseAmount(price);
    if (major === null) {
      setFailed(true);
      return;
    }
    try {
      const done = await api.answerPrice(token, batchId, choice, toMinor(major, me.country));
      const key = choice === "now" ? "price.confirm_now" : "price.confirm_later";
      onDone([
        t(key, {
          name: done.name,
          old: money(done.old),
          new: money(done.new ?? 0),
        }),
      ]);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) onDone([t("stock.done_before")]);
      else setFailed(true);
    }
  }

  return (
    <form className="alert-price" onSubmit={(event) => void save(event)}>
      <label>
        <span>{t("console.alert_new_price")}</span>
        <input
          value={price}
          inputMode="decimal"
          required
          maxLength={14}
          dir="ltr"
          autoFocus
          onChange={(e) => setPrice(e.target.value)}
        />
      </label>
      <button type="submit" className="action action-primary">
        {t("console.alert_save_price")}
      </button>
      {failed && <span role="alert">{t("console.save_failed")}</span>}
    </form>
  );
}

function AlertItem({
  me,
  token,
  notice,
  replies,
  onAnswered,
  onDone,
}: {
  me: OwnerMe;
  token: string;
  notice: OwnerNotice;
  replies: string[] | null;
  onAnswered: (notice: OwnerNotice, replies: string[]) => void;
  onDone: (notice: OwnerNotice) => void;
}) {
  const { t } = useLang();
  const [busy, setBusy] = useState(false);
  const [pricing, setPricing] = useState<{
    batch: string;
    choice: "now" | "later";
  } | null>(null);

  async function press(buttonId: string) {
    const [, batch, choice] = PRICE_BUTTON.exec(buttonId) ?? [];
    if (batch && (choice === "now" || choice === "later")) {
      setPricing({ batch, choice });
      return;
    }
    setBusy(true);
    try {
      onAnswered(notice, (await api.tapNotice(token, notice.id, buttonId)).replies);
    } catch (error) {
      const gone = error instanceof ApiError && error.status === 409;
      onAnswered(notice, [t(gone ? "stock.done_before" : "console.move_failed")]);
    }
    setBusy(false);
  }

  return (
    <li className={`waiting-item alert-${notice.kind}`}>
      <p className="waiting-summary" dir="auto">
        {notice.text}
      </p>
      <p className="alert-route">
        {notice.sent ? t("console.alert_sent") : t("console.alert_here")}
      </p>
      {replies ? (
        <div className="alert-replies" role="status">
          {replies.map((reply, index) => (
            <p key={index} dir="auto">
              <WithLink text={reply} />
            </p>
          ))}
        </div>
      ) : pricing ? (
        <PriceForm
          me={me}
          token={token}
          batchId={pricing.batch}
          choice={pricing.choice}
          onDone={(done) => onAnswered(notice, done)}
        />
      ) : (
        notice.buttons.length > 0 && (
          <div className="waiting-actions">
            {notice.buttons.map((choice, index) => (
              <button
                key={choice.id}
                type="button"
                className={`action${index === 0 ? " action-primary" : ""}`}
                disabled={busy}
                onClick={() => void press(choice.id)}
              >
                {choice.title}
              </button>
            ))}
          </div>
        )
      )}
      {(replies || notice.buttons.length === 0) && (
        <button type="button" className="alert-done" onClick={() => onDone(notice)}>
          {t("console.alert_done")}
        </button>
      )}
    </li>
  );
}

export function Alerts({
  me,
  token,
  list,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  list: OwnerNotice[];
  onChange: () => void;
}) {
  const { t } = useLang();
  const [answered, setAnswered] = useState<Answered[]>([]);
  const settled = new Set(answered.map((item) => item.notice.id));
  const waiting = list.filter((notice) => !settled.has(notice.id));
  if (answered.length === 0 && waiting.length === 0) return null;

  function answer(notice: OwnerNotice, replies: string[]) {
    setAnswered((now) => [
      ...now.filter((item) => item.notice.id !== notice.id),
      { notice, replies },
    ]);
    void api.noticeSeen(token, notice.id).catch(() => undefined);
    onChange();
  }

  async function done(notice: OwnerNotice) {
    await api.noticeSeen(token, notice.id).catch(() => undefined);
    setAnswered((now) => now.filter((item) => item.notice.id !== notice.id));
    onChange();
  }

  return (
    <section className="waiting alerts" aria-labelledby="alerts-title">
      <h2 id="alerts-title" className="waiting-title">
        {t("console.alerts_title")}
        <span className="console-count">{waiting.length}</span>
      </h2>
      <ul className="waiting-list">
        {[...answered.map((item) => item.notice), ...waiting].map((notice) => (
          <AlertItem
            key={notice.id}
            me={me}
            token={token}
            notice={notice}
            replies={answered.find((item) => item.notice.id === notice.id)?.replies ?? null}
            onAnswered={answer}
            onDone={(gone) => void done(gone)}
          />
        ))}
      </ul>
    </section>
  );
}

// -- stock came in: the form and the sheet ----------------------------------------------------

type Line = {
  key: number;
  variant: string;
  quantity: string;
  cost: string;
  expiry: string;
};

const blankLine = (key: number): Line => ({
  key,
  variant: "",
  quantity: "",
  cost: "",
  expiry: "",
});

function LineFields({
  line,
  choices,
  onChange,
  onRemove,
}: {
  line: Line;
  choices: Choice[];
  onChange: (line: Line) => void;
  onRemove: (() => void) | null;
}) {
  const { t } = useLang();
  const set =
    (field: keyof Omit<Line, "key">) =>
    (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      onChange({ ...line, [field]: event.target.value });
  return (
    <fieldset className="restock-line">
      <label className="restock-item">
        <span>{t("console.restock_item")}</span>
        <select value={line.variant} required onChange={set("variant")}>
          <option value="">{t("console.restock_pick")}</option>
          {choices.map((choice) => (
            <option key={choice.id} value={choice.id}>
              {choice.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{t("console.restock_quantity")}</span>
        <input
          value={line.quantity}
          inputMode="numeric"
          required
          maxLength={7}
          dir="ltr"
          onChange={set("quantity")}
        />
      </label>
      <label>
        <span>{t("console.restock_cost")}</span>
        <input
          value={line.cost}
          inputMode="decimal"
          maxLength={14}
          dir="ltr"
          onChange={set("cost")}
        />
      </label>
      <label>
        <span>{t("console.restock_expiry")}</span>
        <input type="date" value={line.expiry} onChange={set("expiry")} />
      </label>
      {onRemove && (
        <button type="button" className="entry-undo restock-remove" onClick={onRemove}>
          {t("console.restock_remove")}
        </button>
      )}
    </fieldset>
  );
}

function RestockForm({
  me,
  token,
  choices,
  onSaved,
}: {
  me: OwnerMe;
  token: string;
  choices: Choice[];
  onSaved: () => void;
}) {
  const { t } = useLang();
  const [lines, setLines] = useState<Line[]>([blankLine(0)]);
  const [supplier, setSupplier] = useState("");
  const [phone, setPhone] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "failed" | number>("idle");

  function toLine(line: Line): RestockLine | null {
    const quantity = wholeNumber(line.quantity);
    const cost = line.cost.trim() ? parseAmount(line.cost) : null;
    if (!line.variant || quantity === null || (line.cost.trim() && cost === null)) return null;
    return {
      variant_id: line.variant,
      quantity,
      total_cost: cost === null ? null : toMinor(cost, me.country),
      expires_on: line.expiry || null,
    };
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const ready = lines.map(toLine);
    if (ready.some((line) => line === null)) {
      setState("failed");
      return;
    }
    setState("saving");
    try {
      const made = await api.restock(token, {
        lines: ready as RestockLine[],
        supplier: supplier.trim() || undefined,
        supplier_phone: phone.trim() || undefined,
      });
      setLines([blankLine(Date.now())]);
      setState(made.batches);
      onSaved();
    } catch {
      setState("failed");
    }
  }

  return (
    <form className="books-form restock-form" onSubmit={(event) => void save(event)}>
      <h3 className="pay-block-title">{t("console.restock_title")}</h3>
      {lines.map((line, index) => (
        <LineFields
          key={line.key}
          line={line}
          choices={choices}
          onChange={(next) => setLines((now) => now.map((old, i) => (i === index ? next : old)))}
          onRemove={
            lines.length > 1 ? () => setLines((now) => now.filter((_, i) => i !== index)) : null
          }
        />
      ))}
      <button
        type="button"
        className="entry-undo"
        onClick={() => setLines((now) => [...now, blankLine(Date.now())])}
      >
        {t("console.restock_another")}
      </button>
      <label>
        <span>{t("console.restock_supplier")}</span>
        <input
          value={supplier}
          maxLength={120}
          dir="auto"
          onChange={(e) => setSupplier(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.restock_phone")}</span>
        <input
          value={phone}
          type="tel"
          maxLength={20}
          dir="ltr"
          onChange={(e) => setPhone(e.target.value)}
        />
      </label>
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving"}
        >
          {t("stock.confirm")}
        </button>
        {typeof state === "number" && (
          <span role="status">{t("console.restock_saved", { count: state })}</span>
        )}
        {state === "failed" && <span role="alert">{t("console.save_failed")}</span>}
      </div>
    </form>
  );
}

type SheetLine = {
  row: number;
  item: string;
  variant: string;
  quantity: string;
  cost: number | null;
  expiry: string | null;
};

function SheetUpload({
  me,
  token,
  choices,
  onSaved,
}: {
  me: OwnerMe;
  token: string;
  choices: Choice[];
  onSaved: () => void;
}) {
  const { t, lang } = useLang();
  const [reading, setReading] = useState<SheetReading | null>(null);
  const [rows, setRows] = useState<SheetLine[]>([]);
  const [state, setState] = useState<"idle" | "reading" | "failed" | "saving" | number>("idle");

  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setState("reading");
    try {
      const read = await api.readSheet(token, file);
      setReading(read);
      setRows(
        read.rows.map((row) => ({
          row: row.row,
          item: row.item,
          variant: row.variant_id ?? "",
          quantity: row.quantity === null ? "" : String(row.quantity),
          cost: row.total_cost,
          expiry: row.expires_on,
        })),
      );
      setState("idle");
    } catch {
      setReading(null);
      setState("failed");
    }
  }

  const ready = rows.filter((row) => row.variant && wholeNumber(row.quantity) !== null);

  async function add() {
    setState("saving");
    try {
      const made = await api.restock(token, {
        source: "sheet",
        supplier: reading?.supplier ?? undefined,
        lines: ready.map((row) => ({
          variant_id: row.variant,
          quantity: wholeNumber(row.quantity) ?? 0,
          total_cost: row.cost,
          expires_on: row.expiry,
        })),
      });
      setReading(null);
      setRows([]);
      setState(made.batches);
      onSaved();
    } catch {
      setState("failed");
    }
  }

  const update = (index: number, change: Partial<SheetLine>) =>
    setRows((now) => now.map((row, i) => (i === index ? { ...row, ...change } : row)));

  return (
    <div className="books-form sheet-upload">
      <h3 className="pay-block-title">{t("console.sheet_title")}</h3>
      <p className="pay-note">{t("console.sheet_lede")}</p>
      <label className="action sheet-choose">
        {state === "reading" ? t("console.sheet_reading") : t("console.sheet_choose")}
        <input
          type="file"
          accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          className="sr-only"
          onChange={(event) => void choose(event)}
        />
      </label>
      {state === "failed" && <p role="alert">{t("console.sheet_failed")}</p>}
      {typeof state === "number" && (
        <p role="status">{t("console.restock_saved", { count: state })}</p>
      )}
      {reading && (
        <>
          {rows.length > ready.length && <p className="pay-note">{t("console.sheet_check")}</p>}
          <ol className="sheet-rows">
            {rows.map((row, index) => {
              const fine = row.variant && wholeNumber(row.quantity) !== null;
              return (
                <li key={row.row} className={fine ? undefined : "is-unready"}>
                  <span className="sheet-item" dir="auto">
                    {row.item}
                    {row.cost !== null && (
                      <span className="sheet-detail">
                        {t("console.restock_cost")}: {formatMoney(row.cost, me.country, lang)}
                      </span>
                    )}
                    {row.expiry && (
                      <span className="sheet-detail">
                        {t("console.restock_expiry")}: {row.expiry}
                      </span>
                    )}
                  </span>
                  <select
                    aria-label={t("console.restock_item")}
                    value={row.variant}
                    onChange={(e) => update(index, { variant: e.target.value })}
                  >
                    <option value="">{t("console.sheet_leave_out")}</option>
                    {choices.map((choice) => (
                      <option key={choice.id} value={choice.id}>
                        {choice.label}
                      </option>
                    ))}
                  </select>
                  <input
                    aria-label={t("console.restock_quantity")}
                    value={row.quantity}
                    inputMode="numeric"
                    maxLength={7}
                    dir="ltr"
                    onChange={(e) => update(index, { quantity: e.target.value })}
                  />
                </li>
              );
            })}
          </ol>
          <button
            type="button"
            className="button button-ink button-small"
            disabled={ready.length === 0 || state === "saving"}
            onClick={() => void add()}
          >
            {t("console.sheet_add", { count: ready.length })}
          </button>
        </>
      )}
    </div>
  );
}

// -- batches ---------------------------------------------------------------------------------

function Meter({ left, came, stale }: { left: number; came: number; stale: boolean }) {
  const share = came > 0 ? Math.min(left / came, 1) : 0;
  return (
    <span className={`batch-meter${stale ? " is-stale" : ""}`} aria-hidden="true">
      <span style={{ inlineSize: `${Math.round(share * 100)}%` }} />
    </span>
  );
}

function BatchRow({
  me,
  token,
  batch,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  batch: Batch;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  const [mode, setMode] = useState<"idle" | "expiry" | "discard">("idle");
  const [expiry, setExpiry] = useState(batch.expires_on ?? "");
  const [reason, setReason] = useState<DiscardReason>("expired");
  const [failed, setFailed] = useState(false);
  const money = (minor: number) => formatMoney(minor, me.country, lang);
  const day = (iso: string) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" });
  const open = batch.status === "open";
  const canDiscard = batch.status !== "discarded" && batch.units_left > 0;

  async function act(work: () => Promise<unknown>) {
    setFailed(false);
    try {
      await work();
      setMode("idle");
      onChange();
    } catch {
      setFailed(true);
    }
  }

  const facts: ReactNode[] = [
    t("console.batch_came", { date: day(batch.received_on) }),
    batch.unit_cost === null
      ? t("console.batch_cost_unknown")
      : t("console.batch_each", { cost: money(batch.unit_cost) }),
  ];
  if (batch.supplier) facts.push(t("console.batch_from", { supplier: batch.supplier }));
  if (batch.expires_on) facts.push(t("console.batch_expires", { date: day(batch.expires_on) }));

  return (
    <li className={`batch is-${batch.status}`}>
      <div className="batch-head">
        <span className="batch-name" dir="auto">
          {batch.name}
        </span>
        <span className="batch-left">
          {t("console.batch_left", { left: batch.units_left, came: batch.quantity })}
        </span>
      </div>
      <Meter left={batch.units_left} came={batch.quantity} stale={!open} />
      <p className="batch-facts">
        {facts.map((fact, index) => (
          <span key={index} dir="auto">
            {fact}
          </span>
        ))}
        {batch.status !== "open" && (
          <span className="batch-tag">{t(`console.batch_status.${batch.status}` as Key)}</span>
        )}
        {(batch.price_question === "open" || batch.price_question === "later") && (
          <span className="batch-tag">
            {t(`console.batch_price.${batch.price_question}` as Key)}
          </span>
        )}
      </p>
      {batch.revenue !== null && batch.sold > 0 && (
        <p className="batch-money">
          {t("console.batch_sold", {
            sold: batch.sold,
            revenue: money(batch.revenue),
            profit: batch.profit === null ? "-" : money(batch.profit),
          })}
        </p>
      )}
      {mode === "expiry" && (
        <form
          className="batch-edit"
          onSubmit={(event) => {
            event.preventDefault();
            void act(() => api.setExpiry(token, batch.id, expiry));
          }}
        >
          <label>
            <span>{t("console.restock_expiry")}</span>
            <input
              type="date"
              value={expiry}
              required
              onChange={(e) => setExpiry(e.target.value)}
            />
          </label>
          <button type="submit" className="action action-primary">
            {t("console.save")}
          </button>
          <button type="button" className="entry-undo" onClick={() => setMode("idle")}>
            {t("owner.cancel")}
          </button>
        </form>
      )}
      {mode === "discard" && (
        <div className="batch-edit">
          <label>
            <span>{t("console.batch_reason")}</span>
            <select value={reason} onChange={(e) => setReason(e.target.value as DiscardReason)}>
              {REASONS.map((option) => (
                <option key={option} value={option}>
                  {t(`console.batch_reason.${option}` as Key)}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="action action-danger"
            onClick={() => void act(() => api.discardBatch(token, batch.id, reason))}
          >
            {t("console.batch_discard_confirm", { units: batch.units_left })}
          </button>
          <button type="button" className="entry-undo" onClick={() => setMode("idle")}>
            {t("owner.cancel")}
          </button>
        </div>
      )}
      {mode === "idle" && (open || canDiscard) && (
        <div className="batch-actions">
          {open && (
            <button type="button" className="entry-undo" onClick={() => setMode("expiry")}>
              {t("console.batch_set_expiry")}
            </button>
          )}
          {canDiscard && (
            <button
              type="button"
              className="entry-undo"
              onClick={() => {
                setReason(open ? "damaged" : "expired");
                setMode("discard");
              }}
            >
              {t("console.batch_discard")}
            </button>
          )}
        </div>
      )}
      {failed && (
        <p className="order-failed" role="alert">
          {t("console.move_failed")}
        </p>
      )}
    </li>
  );
}

function ProfitTable({ me, rows }: { me: OwnerMe; rows: ProductProfit[] }) {
  const { t, lang } = useLang();
  const money = (minor: number) => formatMoney(minor, me.country, lang);
  if (rows.length === 0) return <p className="aside">{t("console.profit_none")}</p>;
  return (
    <table className="daybook profit-table">
      <thead>
        <tr>
          <td />
          <th scope="col">{t("console.profit_sold")}</th>
          <th scope="col">{t("console.profit_sales")}</th>
          <th scope="col">{t("console.profit_cost")}</th>
          <th scope="col">{t("console.profit_profit")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.product_id}>
            <th scope="row" dir="auto">
              {row.name}
              {row.cost_unknown > 0 && (
                <span className="profit-unknown">
                  {t("console.profit_unknown", { count: row.cost_unknown })}
                </span>
              )}
            </th>
            <td>{row.sold}</td>
            <td>{money(row.revenue)}</td>
            <td>{money(row.cost)}</td>
            <td className={row.profit < 0 ? "is-loss" : undefined}>{money(row.profit)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function StockPanel({
  me,
  token,
  products,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  products: OwnerProduct[] | null;
  onChange: () => void;
}) {
  const { t } = useLang();
  const canBooks = me.rights.includes("books");
  const [all, setAll] = useState(false);
  const loadBatches = useCallback(() => api.batches(token), [token]);
  const loadProfit = useCallback(() => api.profit(token), [token]);
  const batches = usePolled(loadBatches, POLL_MS);
  const profit = usePolled(canBooks ? loadProfit : null, POLL_MS);
  const choices = variantChoices(products);
  const changed = useCallback(() => {
    batches.refresh();
    profit.refresh();
    onChange();
  }, [batches, profit, onChange]);
  const list = batches.data ?? [];
  const shown = all ? list : list.slice(0, BATCHES_SHOWN);

  return (
    <section className="stock-panel" aria-labelledby="stock-panel-title">
      <h2 id="stock-panel-title" className="section-title">
        {t("console.stock_title")}
      </h2>
      <p className="aside">{t("console.stock_lede")}</p>
      <div className="pay-columns stock-columns">
        <div className="pay-block">
          <RestockForm me={me} token={token} choices={choices} onSaved={changed} />
        </div>
        <div className="pay-block">
          <SheetUpload me={me} token={token} choices={choices} onSaved={changed} />
        </div>
      </div>
      <h3 className="pay-block-title stock-subtitle">{t("console.batches_title")}</h3>
      {batches.data && list.length === 0 && <p className="aside">{t("console.batches_none")}</p>}
      <ul className="batches" aria-busy={!batches.data}>
        {shown.map((batch) => (
          <BatchRow key={batch.id} me={me} token={token} batch={batch} onChange={changed} />
        ))}
      </ul>
      {list.length > shown.length && (
        <button type="button" className="entry-undo batches-more" onClick={() => setAll(true)}>
          {t("console.batches_more", { count: list.length })}
        </button>
      )}
      {canBooks && profit.data && (
        <>
          <h3 className="pay-block-title stock-subtitle">{t("console.profit_title")}</h3>
          <ProfitTable me={me} rows={profit.data} />
        </>
      )}
    </section>
  );
}
