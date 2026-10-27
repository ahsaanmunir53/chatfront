// The owner's side of payments: payments to check with the evidence and Approve and Reject
// (feature 25), orders waiting for money, money received with its signed receipt, and the
// one-time setup of SMS forwarding (feature 24).
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Forwarding, OwnerMe, PaymentReview, Payments } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { useServer } from "../server";

const POLL_MS = 5000;
const DASH = "—";

/** Polls the payments panel; refresh() after a decision. */
export function usePayments(token: string) {
  const { state } = useServer();
  const [data, setData] = useState<Payments | null>(null);
  const refresh = useCallback(() => {
    api.payments(token).then(setData, () => {
      // The next poll tries again.
    });
  }, [token]);
  useEffect(() => {
    if (state !== "online") return;
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(timer);
  }, [state, refresh]);
  return { data, refresh };
}

function useMoney(me: OwnerMe) {
  const { lang } = useLang();
  return (minor: number | null) => (minor === null ? DASH : formatMoney(minor, me.country, lang));
}

function timeOf(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function Evidence({ me, review }: { me: OwnerMe; review: PaymentReview }) {
  const { t } = useLang();
  const money = useMoney(me);
  return (
    <div className="pay-evidence">
      <ul className="pay-facts">
        <li>{t("payment.asked", { amount: money(review.asked) })}</li>
        {review.screenshot_url && (
          <li>
            {t("payment.screenshot", {
              amount: money(review.screenshot_amount),
              provider: review.provider ?? DASH,
              tid: review.transaction_id ?? DASH,
            })}
          </li>
        )}
        <li>
          {review.sms_amount === null
            ? t("payment.no_sms")
            : t("payment.sms_match", {
                amount: money(review.sms_amount),
                tid: review.transaction_id ?? DASH,
              })}
        </li>
      </ul>
      {review.signals.length > 0 && (
        <ul className="pay-signals">
          {review.signals.map((signal) => (
            <li key={signal.code} dir="auto">
              {t(`payment.signal.${signal.code}` as Key, { detail: signal.detail })}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Payments the owner has to decide, above everything else when there are any. */
export function PaymentReviews({
  me,
  token,
  data,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  data: Payments | null;
  onChange: () => void;
}) {
  const { t } = useLang();
  const [busy, setBusy] = useState<string | null>(null);
  const reviews = data?.reviews ?? [];
  if (reviews.length === 0) return null;

  async function decide(review: PaymentReview, answer: "approve" | "reject") {
    setBusy(review.id);
    await api.decidePayment(token, review.id, answer).catch(() => undefined);
    setBusy(null);
    onChange();
  }

  return (
    <section className="pay-review" aria-labelledby="pay-review-title">
      <h2 id="pay-review-title" className="waiting-title">
        {t("console.payments_review")}
        <span className="console-count">{reviews.length}</span>
      </h2>
      <ul className="waiting-list">
        {reviews.map((review) => (
          <li key={review.id} className="waiting-item pay-item">
            {review.screenshot_url && (
              <a className="pay-shot" href={review.screenshot_url} target="_blank" rel="noopener">
                <img src={review.screenshot_url} alt={t("console.payments_screenshot")} />
              </a>
            )}
            <div className="pay-body">
              <p className="waiting-head">
                <strong dir="auto">{review.customer || t("console.customer_label")}</strong>
                <span>{t("receipt.order", { code: review.order_code })}</span>
                <time>{timeOf(review.created_at)}</time>
              </p>
              <Evidence me={me} review={review} />
              <div className="waiting-actions">
                <button
                  type="button"
                  className="action action-primary"
                  disabled={busy === review.id}
                  onClick={() => void decide(review, "approve")}
                >
                  {t("payment.approve")}
                </button>
                <button
                  type="button"
                  className="action"
                  disabled={busy === review.id}
                  onClick={() => void decide(review, "reject")}
                >
                  {t("payment.reject")}
                </button>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SmsForwarding({
  token,
  on,
  onChange,
}: {
  token: string;
  on: boolean;
  onChange: () => void;
}) {
  const { t } = useLang();
  const [made, setMade] = useState<Forwarding | null>(null);
  const [copied, setCopied] = useState(false);

  async function makeLink() {
    try {
      setMade(await api.setUpForwarding(token));
      setCopied(false);
      onChange();
    } catch {
      // The button stays; the owner can try again.
    }
  }

  async function copy(text: string) {
    await navigator.clipboard?.writeText(text).catch(() => undefined);
    setCopied(true);
  }

  return (
    <div className="pay-block pay-sms">
      <h3 className="pay-block-title">{t("console.sms_title")}</h3>
      <p className={on ? "pay-on" : "pay-off"}>{on ? t("console.sms_on") : t("console.sms_off")}</p>
      {made ? (
        <div className="pay-link">
          <p>{t("console.sms_steps")}</p>
          <code dir="ltr">{made.url}</code>
          <button type="button" className="action" onClick={() => void copy(made.url)}>
            {copied ? t("console.copied") : t("console.copy")}
          </button>
        </div>
      ) : (
        <button type="button" className="action" onClick={() => void makeLink()}>
          {on ? t("console.sms_new") : t("console.sms_setup")}
        </button>
      )}
    </div>
  );
}

/** Orders waiting for money, money received, and SMS forwarding. */
export function PaymentsPanel({
  me,
  token,
  data,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  data: Payments | null;
  onChange: () => void;
}) {
  const { t } = useLang();
  const money = useMoney(me);
  if (!data) return null;
  return (
    <section className="pay-panel" aria-labelledby="pay-panel-title">
      <h2 id="pay-panel-title" className="section-title">
        {t("console.payments_title")}
      </h2>
      {data.reviews.length === 0 && <p className="aside">{t("console.payments_none")}</p>}
      <div className="pay-columns">
        <div className="pay-block">
          <h3 className="pay-block-title">
            {t("console.payments_waiting")}
            <span className="console-count">{data.waiting.length}</span>
          </h3>
          <ul className="pay-rows">
            {data.waiting.map((order) => (
              <li key={order.order_code}>
                <span className="pay-code">{order.order_code}</span>
                <span dir="auto">{order.customer}</span>
                <span className="pay-amount">
                  {t("console.payments_due", { amount: money(order.due_now) })}
                </span>
                {order.paid > 0 && (
                  <span className="pay-note">
                    {t("console.payments_paid_of", {
                      paid: money(order.paid),
                      total: money(order.total),
                    })}
                  </span>
                )}
                {order.reminded && (
                  <span className="pay-note">{t("console.payments_reminded")}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
        <div className="pay-block">
          <h3 className="pay-block-title">
            {t("console.payments_recent")}
            <span className="console-count">{data.recent.length}</span>
          </h3>
          <ul className="pay-rows">
            {data.recent.map((paid) => (
              <li key={`${paid.order_code}-${paid.created_at}`}>
                <span className="pay-code">{paid.order_code}</span>
                <span className="pay-amount">{money(paid.amount)}</span>
                <span className="pay-note">
                  {paid.verified_by === "sms"
                    ? t("console.payments_by_sms")
                    : t("console.payments_by_owner")}
                </span>
                {paid.receipt_code && (
                  <Link to={`/r/${paid.receipt_code}`} className="pay-receipt">
                    {t("console.payments_receipt")}
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </div>
        <SmsForwarding token={token} on={data.forwarding} onChange={onChange} />
      </div>
    </section>
  );
}
