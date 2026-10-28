// Money and goods coming back: refunds owed, closed with the owner's transfer screenshot as
// proof (feature 28), and the swaps and returns customers asked for, with Approve and Reject,
// the return label for a reverse pickup and Parcel received (feature 92).
import { useCallback, useState } from "react";
import type { ChangeEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { OwnerMe, OwnerReturn, Refund, RefundResult } from "../api";
import { saveFile } from "../download";
import { isolate } from "../i18n";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 8000;

function dayOf(iso: string, lang: string): string {
  return new Date(iso).toLocaleDateString(lang, { day: "numeric", month: "short" });
}

type Closed = { code: string; told: boolean };

function DueRefund({
  me,
  token,
  refund,
  onClosed,
}: {
  me: OwnerMe;
  token: string;
  refund: Refund;
  onClosed: (closed: Closed) => void;
}) {
  const { t, lang } = useLang();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RefundResult | null>(null);
  const [failed, setFailed] = useState(false);

  async function run(step: () => Promise<RefundResult>) {
    setBusy(true);
    setFailed(false);
    try {
      const done = await step();
      setResult(done);
      if (done.outcome === "sent") onClosed({ code: refund.order_code, told: done.customer_told });
    } catch {
      setFailed(true);
    }
    setBusy(false);
  }

  function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void run(() => api.refundProof(token, refund.id, file));
  }

  const to = refund.to_name ?? t("console.customer_label");
  return (
    <li className="waiting-item refund-item">
      <p className="waiting-head">
        <strong className="refund-amount">{formatMoney(refund.amount, me.country, lang)}</strong>
        <span>{t("receipt.order", { code: isolate(refund.order_code) })}</span>
        <time dateTime={refund.created_at}>{dayOf(refund.created_at, lang)}</time>
      </p>
      <p className="refund-to" dir="auto">
        {t("console.refund_to", { name: to, wallet: refund.to_provider ?? "none" })}
      </p>
      <p className="refund-why">
        <span>{t("refund.reason", { reason: refund.reason })}</span>
        {refund.return_code && (
          <span className="pay-note">
            {t("console.refund_return", { code: refund.return_code })}
          </span>
        )}
      </p>
      {result?.outcome === "check" && (
        <div className="refund-check" role="alert">
          <p>{t("console.refund_check")}</p>
          <ul className="pay-signals">
            {result.signals.map((signal) => (
              <li key={signal.code} dir="auto">
                {t(`refund.signal.${signal.code}` as Key, { detail: signal.detail })}
              </li>
            ))}
          </ul>
          <p className="aside">{t("console.refund_record_note")}</p>
        </div>
      )}
      <div className="waiting-actions">
        <label className={`action action-primary refund-upload${busy ? " is-busy" : ""}`}>
          {busy ? t("console.refund_reading") : t("console.refund_proof")}
          <input type="file" accept="image/*" disabled={busy} onChange={upload} />
        </label>
        <button
          type="button"
          className="action"
          disabled={busy}
          onClick={() => void run(() => api.recordRefund(token, refund.id))}
        >
          {t("console.refund_record")}
        </button>
      </div>
      {failed && (
        <p className="order-failed" role="alert">
          {t("console.refund_failed")}
        </p>
      )}
    </li>
  );
}

/** Refunds owed, and the latest sent with their receipts; nothing when there are none. */
export function RefundsPanel({ me, token }: { me: OwnerMe; token: string }) {
  const { t, lang } = useLang();
  const load = useCallback(() => api.refunds(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [closed, setClosed] = useState<Closed | null>(null);
  const close = useCallback(
    (done: Closed) => {
      setClosed(done);
      refresh();
    },
    [refresh],
  );
  if (!data || (data.due.length === 0 && data.sent.length === 0)) return null;
  return (
    <section className="pay-panel refunds" aria-labelledby="refunds-title">
      <h2 id="refunds-title" className="section-title">
        {t("console.refunds_title")}
        {data.due.length > 0 && <span className="console-count">{data.due.length}</span>}
      </h2>
      <p className="aside">{t("console.refunds_lede")}</p>
      {closed && (
        <p className={closed.told ? "pay-on" : "order-failed"} role="status">
          {closed.told
            ? t("console.refund_done", { code: closed.code })
            : t("console.refund_not_told", { code: closed.code })}
        </p>
      )}
      <div className="pay-columns refund-columns">
        {data.due.length > 0 && (
          <ul className="waiting-list pay-block">
            {data.due.map((refund) => (
              <DueRefund key={refund.id} me={me} token={token} refund={refund} onClosed={close} />
            ))}
          </ul>
        )}
        {data.sent.length > 0 && (
          <div className="pay-block">
            <h3 className="pay-block-title">{t("console.refunds_sent")}</h3>
            <ul className="pay-rows">
              {data.sent.map((refund) => (
                <li key={refund.id}>
                  <span className="pay-code">{refund.order_code}</span>
                  <span className="pay-amount">{formatMoney(refund.amount, me.country, lang)}</span>
                  <span className="pay-note">
                    {refund.verified_by === "proof"
                      ? t("console.refund_by_proof")
                      : t("console.refund_by_owner")}
                  </span>
                  {refund.receipt_code && (
                    <Link to={`/r/${refund.receipt_code}`} className="pay-receipt">
                      {t("console.payments_receipt")}
                    </Link>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}

function ReturnRow({
  token,
  item,
  canShip,
  onChange,
}: {
  token: string;
  item: OwnerReturn;
  canShip: boolean;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function run(step: () => Promise<unknown>) {
    setBusy(true);
    setFailed(false);
    await step().then(onChange, () => setFailed(true));
    setBusy(false);
  }

  const approval = item.approval_id;
  const label = async () =>
    saveFile(await api.returnLabel(token, item.id), `return-${item.code}.pdf`);
  return (
    <li className={`waiting-item return-item${item.photo_url ? " has-photo" : ""}`}>
      {item.photo_url && (
        <a className="pay-shot" href={item.photo_url} target="_blank" rel="noopener">
          <img src={item.photo_url} alt={t("console.return_photo")} />
        </a>
      )}
      <div className="pay-body">
        <p className="waiting-head">
          <strong>{t("console.return_kind", { kind: item.kind, code: item.code })}</strong>
          <span>{t("receipt.order", { code: isolate(item.order_code) })}</span>
          <span dir="auto">{item.customer}</span>
          <time dateTime={item.created_at}>{dayOf(item.created_at, lang)}</time>
        </p>
        <p className={`return-status is-${item.status}`}>
          {t(`console.return_status.${item.status}` as Key)}
        </p>
        <ul className="return-lines">
          {item.lines.map((line) => (
            <li key={`${line.name}-${line.to ?? ""}`}>
              <span dir="auto">
                {line.quantity} × {line.name}
              </span>
              {line.to && (
                <span className="return-wants" dir="auto">
                  {t("console.return_wants", { option: line.to })}
                </span>
              )}
            </li>
          ))}
        </ul>
        <p className="refund-why">{t(`return.reason.${item.reason_code}` as Key)}</p>
        {item.reason && (
          <p className="return-said" dir="auto">
            {t("console.return_said", { words: item.reason })}
          </p>
        )}
        {item.exchange_order_code && (
          <p className="pay-note">
            {t("console.return_new_order", { code: item.exchange_order_code })}
          </p>
        )}
        <div className="waiting-actions">
          {approval && (
            <>
              <button
                type="button"
                className="action action-primary"
                disabled={busy}
                onClick={() => void run(() => api.answerApproval(token, approval, true))}
              >
                {t("payment.approve")}
              </button>
              <button
                type="button"
                className="action"
                disabled={busy}
                onClick={() => void run(() => api.answerApproval(token, approval, false))}
              >
                {t("payment.reject")}
              </button>
            </>
          )}
          {item.status === "approved" && canShip && (
            <>
              <button type="button" className="action" onClick={() => void run(label)}>
                {t("console.return_label")}
              </button>
              <button
                type="button"
                className="action action-primary"
                disabled={busy}
                onClick={() => void run(() => api.returnReceived(token, item.id))}
              >
                {t("console.return_received")}
              </button>
            </>
          )}
        </div>
        {failed && (
          <p className="order-failed" role="alert">
            {t("console.refund_failed")}
          </p>
        )}
      </div>
    </li>
  );
}

/** What customers asked to swap or send back; nothing when there is none. */
export function ReturnsPanel({ token, canShip }: { token: string; canShip: boolean }) {
  const { t } = useLang();
  const load = useCallback(() => api.returns(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  if (!data || data.length === 0) return null;
  return (
    <section className="pay-panel returns" aria-labelledby="returns-title">
      <h2 id="returns-title" className="section-title">
        {t("console.returns_title")}
      </h2>
      <p className="aside">{t("console.returns_lede")}</p>
      <ul className="waiting-list pay-block">
        {data.map((item) => (
          <ReturnRow key={item.id} token={token} item={item} canShip={canShip} onChange={refresh} />
        ))}
      </ul>
    </section>
  );
}
