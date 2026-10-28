// The owner's orders: where each one stands, what is paid and what the rider collects, the
// risk of a cash-on-delivery order coming back with its reasons (feature 34), the next moves,
// the shipping label (feature 101), what the courier charged (51), the packing check (102),
// the day's pick list and slips (103), and why each decision was made (feature 107).
import { useCallback, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { ApiError, api } from "../api";
import type { OwnerMe, OwnerOrder, PackingCheck } from "../api";
import { saveFile } from "../download";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";
import { formatMoney, parseAmount, toMajor, toMinor } from "../money";
import { PinIcon } from "./Icons";
import { WhyDetails } from "./Why";

const POLL_MS = 6000;
const PICK_POLL_MS = 30_000;
const CHECKABLE = new Set(["paid", "confirmed", "packed"]);
const SENT = new Set(["dispatched", "delivered", "returned"]);

/** Polls the shop's orders, for people who see orders; refresh() after a move. */
export function useOrders(token: string, enabled: boolean) {
  const load = useCallback(() => api.orders(token), [token]);
  const { data, refresh } = usePolled(enabled ? load : null, POLL_MS);
  return { orders: data, refresh };
}

async function downloadLabel(token: string, code: string) {
  saveFile(await api.label(token, code), `label-${code}.pdf`);
}

function Risk({ order }: { order: OwnerOrder }) {
  const { t } = useLang();
  const { band, reasons } = order.risk;
  if (order.method !== "cod" && band === "low") return null;
  return (
    <details className={`order-risk risk-${band}`}>
      <summary>{t(`risk.${band}` as Key)}</summary>
      <ul>
        {reasons.map((reason) => (
          <li key={reason.code}>{t(`risk.${reason.code}` as Key, { count: reason.count ?? 0 })}</li>
        ))}
      </ul>
    </details>
  );
}

/** A typed amount in minor units; null for nothing typed, undefined for something unreadable. */
function typedMinor(typed: string, country: string): number | null | undefined {
  if (!typed.trim()) return null;
  const major = parseAmount(typed);
  return major === null ? undefined : toMinor(major, country);
}

function Dispatch({
  me,
  onSend,
}: {
  me: OwnerMe;
  onSend: (courier: string, tracking: string, charge: number | null) => void;
}) {
  const { t } = useLang();
  const [courier, setCourier] = useState("");
  const [tracking, setTracking] = useState("");
  const [charge, setCharge] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    const minor = typedMinor(charge, me.country);
    if (minor !== undefined) onSend(courier, tracking, minor);
  }
  return (
    <form className="order-dispatch" onSubmit={submit}>
      <label>
        <span>{t("console.order_courier")}</span>
        <input value={courier} maxLength={60} onChange={(e) => setCourier(e.target.value)} />
      </label>
      <label>
        <span>{t("console.order_tracking")}</span>
        <input
          value={tracking}
          maxLength={60}
          dir="ltr"
          onChange={(e) => setTracking(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.order_charge")}</span>
        <input value={charge} inputMode="decimal" onChange={(e) => setCharge(e.target.value)} />
      </label>
      <button type="submit" className="action action-primary">
        {t("console.move.dispatched")}
      </button>
    </form>
  );
}

/** What the courier charged, out and back, entered or corrected after the fact (51). */
function Charges({
  me,
  token,
  order,
  onDone,
}: {
  me: OwnerMe;
  token: string;
  order: OwnerOrder;
  onDone: () => void;
}) {
  const { t } = useLang();
  const shown = (minor: number | null | undefined) =>
    minor == null ? "" : String(toMajor(minor, me.country));
  const [there, setThere] = useState(shown(order.shipment?.courier_charge));
  const [back, setBack] = useState(shown(order.shipment?.return_charge));
  const [failed, setFailed] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault();
    const out = typedMinor(there, me.country);
    const returned = typedMinor(back, me.country);
    if (out === undefined || returned === undefined) {
      setFailed(true);
      return;
    }
    try {
      await api.setCharges(token, order.code, out, returned);
      setFailed(false);
      onDone();
    } catch {
      setFailed(true);
    }
  }
  return (
    <details className="order-charges">
      <summary>{t("console.charges")}</summary>
      <form className="order-dispatch" onSubmit={(event) => void save(event)}>
        <label>
          <span>{t("console.charges_there")}</span>
          <input value={there} inputMode="decimal" onChange={(e) => setThere(e.target.value)} />
        </label>
        <label>
          <span>{t("console.charges_back")}</span>
          <input value={back} inputMode="decimal" onChange={(e) => setBack(e.target.value)} />
        </label>
        <button type="submit" className="action action-primary">
          {t("console.charges_save")}
        </button>
      </form>
      {failed && (
        <p className="order-failed" role="alert">
          {t("console.charges_failed")}
        </p>
      )}
    </details>
  );
}

/** What a packing check found, one line per problem. */
export function findings(check: PackingCheck, t: ReturnType<typeof useLang>["t"]): string[] {
  const out: string[] = [];
  for (const line of check.lines) {
    const seen = line.seen ?? 0;
    if (!line.sure) out.push(t("packing.not_seen", { name: line.name }));
    else if (seen < line.quantity)
      out.push(t("packing.missing", { name: line.name, count: line.quantity - seen }));
    else if (seen > line.quantity)
      out.push(t("packing.too_many", { name: line.name, count: seen - line.quantity }));
  }
  for (const item of check.extra) out.push(t("packing.extra", { item }));
  if (check.asked_owner) out.push(t("packing.asked_owner"));
  return out;
}

function PackingResult({ check }: { check: PackingCheck }) {
  const { t } = useLang();
  const lines = findings(check, t);
  return (
    <div className={`packing-result is-${check.verdict}`} role="status">
      <strong>{t(`console.packing_${check.verdict}` as Key)}</strong>
      {lines.length > 0 && (
        <ul>
          {lines.map((line) => (
            <li key={line} dir="auto">
              {line}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** A photo of the packed order, checked against it (feature 102). */
function CheckPacking({
  token,
  order,
  onDone,
}: {
  token: string;
  order: OwnerOrder;
  onDone: () => void;
}) {
  const { t } = useLang();
  const [state, setState] = useState<"idle" | "checking" | "failed" | "no_model">("idle");
  async function send(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setState("checking");
    try {
      await api.packingCheck(token, order.code, file);
      setState("idle");
      onDone();
    } catch (error) {
      setState(error instanceof ApiError && error.status === 503 ? "no_model" : "failed");
    }
  }
  return (
    <>
      <label className="action packing-photo" aria-busy={state === "checking"}>
        {state === "checking" ? t("console.packing_checking") : t("console.packing_check")}
        <input
          type="file"
          accept="image/*"
          capture="environment"
          className="sr-only"
          disabled={state === "checking"}
          onChange={(event) => void send(event)}
        />
      </label>
      {state === "failed" && (
        <p className="order-failed" role="alert">
          {t("console.packing_failed")}
        </p>
      )}
      {state === "no_model" && (
        <p className="order-failed" role="alert">
          {t("packing.no_model")}
        </p>
      )}
    </>
  );
}

function OrderRow({
  me,
  token,
  order,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  order: OwnerOrder;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  const canShip = me.rights.includes("ship");
  const [dispatching, setDispatching] = useState(false);
  const [failed, setFailed] = useState(false);
  const money = (minor: number) => formatMoney(minor, me.country, lang);

  async function move(to: string, courier = "", tracking = "", charge: number | null = null) {
    setFailed(false);
    try {
      await api.moveOrder(token, order.code, to, courier, tracking, charge);
      setDispatching(false);
      onChange();
    } catch {
      setFailed(true);
    }
  }

  return (
    <li className="order-row">
      <div className="order-head">
        <span className="pay-code">{order.code}</span>
        <span dir="auto">{order.customer}</span>
        <span className={`order-status status-${order.status}`}>
          {t(`order.status.${order.status}` as Key)}
        </span>
        {order.pin && (
          <span className="order-pin" title={t("console.order_pin")}>
            <PinIcon size={15} />
            <span className="sr-only">{t("console.order_pin")}</span>
          </span>
        )}
      </div>
      <div className="order-money">
        <span className="pay-amount">{money(order.total)}</span>
        {order.paid > 0 && (
          <span className="pay-note">{t("console.order_paid", { amount: money(order.paid) })}</span>
        )}
        {order.to_collect > 0 && (
          <span className="pay-note">
            {t("console.order_to_collect", { amount: money(order.to_collect) })}
          </span>
        )}
        {order.shipment?.courier && (
          <span className="pay-note" dir="auto">
            {order.shipment.courier} {order.shipment.tracking_number ?? ""}
          </span>
        )}
        {order.shipment?.courier_charge != null && (
          <span className="pay-note">
            {t("console.order_courier_charge", { amount: money(order.shipment.courier_charge) })}
          </span>
        )}
        {order.shipment?.return_charge != null && (
          <span className="pay-note">
            {t("console.order_return_charge", { amount: money(order.shipment.return_charge) })}
          </span>
        )}
      </div>
      {order.packing && <PackingResult check={order.packing} />}
      <Risk order={order} />
      <WhyDetails me={me} token={token} code={order.code} />
      {(order.next.length > 0 || order.label) && (
        <div className="waiting-actions">
          {order.next.map((to) =>
            to === "dispatched" ? (
              <button
                key={to}
                type="button"
                className="action"
                aria-expanded={dispatching}
                onClick={() => setDispatching(!dispatching)}
              >
                {t("console.move.dispatched")}
              </button>
            ) : (
              <button key={to} type="button" className="action" onClick={() => void move(to)}>
                {t(`console.move.${to}` as Key)}
              </button>
            ),
          )}
          {order.label && (
            <button
              type="button"
              className="action"
              onClick={() => void downloadLabel(token, order.code).catch(() => setFailed(true))}
            >
              {t("console.order_label")}
            </button>
          )}
          {canShip && CHECKABLE.has(order.status) && (
            <CheckPacking token={token} order={order} onDone={onChange} />
          )}
        </div>
      )}
      {dispatching && (
        <Dispatch
          me={me}
          onSend={(courier, tracking, charge) => void move("dispatched", courier, tracking, charge)}
        />
      )}
      {canShip && order.shipment && SENT.has(order.status) && (
        <Charges
          key={`${order.shipment.courier_charge}-${order.shipment.return_charge}`}
          me={me}
          token={token}
          order={order}
          onDone={onChange}
        />
      )}
      {failed && (
        <p className="order-failed" role="alert">
          {t("console.move_failed")}
        </p>
      )}
    </li>
  );
}

/** The day's pick list, and the PDF with a packing slip per order (feature 103). */
function PickListPanel({ token }: { token: string }) {
  const { t } = useLang();
  const load = useCallback(() => api.pickList(token), [token]);
  const { data } = usePolled(load, PICK_POLL_MS);
  const [failed, setFailed] = useState(false);
  if (!data || (data.lines.length === 0 && data.waiting.length === 0)) return null;
  async function print() {
    try {
      saveFile(await api.pickListPdf(token), `pick-list-${data?.day ?? "today"}.pdf`);
    } catch {
      setFailed(true);
    }
  }
  const waiting = data.waiting.map((w) => `${w.code} (${w.ships_from})`).join(", ");
  return (
    <section className="pick-list" aria-labelledby="pick-list-title">
      <h3 id="pick-list-title" className="pay-block-title">
        {t("console.pick_title")}
      </h3>
      <p className="pay-note">{t("pick.title", { orders: data.orders, pieces: data.pieces })}</p>
      {data.lines.length > 0 && (
        <table className="daybook pick-table">
          <thead>
            <tr>
              <th scope="col">{t("console.pick_qty")}</th>
              <th scope="col">{t("console.pick_item")}</th>
              <th scope="col">{t("console.pick_orders")}</th>
            </tr>
          </thead>
          <tbody>
            {data.lines.map((line, index) => (
              <tr key={`${line.name}-${index}`}>
                <td>{line.quantity}</td>
                <th scope="row" dir="auto">
                  {line.name}
                  {line.short > 0 && (
                    <span className="profit-unknown">{t("pick.short", { count: line.short })}</span>
                  )}
                </th>
                <td dir="ltr">{line.orders}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {waiting && (
        <p className="pay-note">
          {t("pick.waiting", { count: data.waiting.length, orders: waiting })}
        </p>
      )}
      {data.lines.length > 0 && (
        <button type="button" className="action" onClick={() => void print()}>
          {t("console.pick_print")}
        </button>
      )}
      {failed && (
        <p className="order-failed" role="alert">
          {t("console.move_failed")}
        </p>
      )}
    </section>
  );
}

export function OrdersPanel({
  me,
  token,
  orders,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  orders: OwnerOrder[] | null;
  onChange: () => void;
}) {
  const { t } = useLang();
  if (!orders) return null;
  return (
    <section className="order-panel" aria-labelledby="order-panel-title">
      <h2 id="order-panel-title" className="section-title">
        {t("console.orders_title")}
        <span className="console-count">{orders.length}</span>
      </h2>
      {me.rights.includes("ship") && <PickListPanel token={token} />}
      {orders.length === 0 ? (
        <p className="aside">{t("console.orders_none")}</p>
      ) : (
        <ul className="order-list">
          {orders.map((order) => (
            <OrderRow key={order.code} me={me} token={token} order={order} onChange={onChange} />
          ))}
        </ul>
      )}
    </section>
  );
}
