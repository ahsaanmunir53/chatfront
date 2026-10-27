// The owner's orders: where each one stands, what is paid and what the rider collects, the
// risk of a cash-on-delivery order coming back with its reasons (feature 34), the next moves,
// and the shipping label (feature 101).
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type { OwnerMe, OwnerOrder } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { useServer } from "../server";
import { PinIcon } from "./Icons";

const POLL_MS = 6000;

/** Polls the owner's orders; refresh() after a move. */
export function useOrders(token: string) {
  const { state } = useServer();
  const [orders, setOrders] = useState<OwnerOrder[] | null>(null);
  const refresh = useCallback(() => {
    api.orders(token).then(setOrders, () => {
      // The next poll tries again.
    });
  }, [token]);
  useEffect(() => {
    if (state !== "online") return;
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [state, refresh]);
  return { orders, refresh };
}

async function downloadLabel(token: string, code: string) {
  const blob = await api.label(token, code);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `label-${code}.pdf`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
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

function Dispatch({ onSend }: { onSend: (courier: string, tracking: string) => void }) {
  const { t } = useLang();
  const [courier, setCourier] = useState("");
  const [tracking, setTracking] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    onSend(courier, tracking);
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
      <button type="submit" className="action action-primary">
        {t("console.move.dispatched")}
      </button>
    </form>
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
  const [dispatching, setDispatching] = useState(false);
  const [failed, setFailed] = useState(false);
  const money = (minor: number) => formatMoney(minor, me.country, lang);

  async function move(to: string, courier = "", tracking = "") {
    setFailed(false);
    try {
      await api.moveOrder(token, order.code, to, courier, tracking);
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
      </div>
      <Risk order={order} />
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
        </div>
      )}
      {dispatching && (
        <Dispatch onSend={(courier, tracking) => void move("dispatched", courier, tracking)} />
      )}
      {failed && (
        <p className="order-failed" role="alert">
          {t("console.move_failed")}
        </p>
      )}
    </li>
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
