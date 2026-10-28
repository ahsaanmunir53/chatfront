// The owner's side of payments: payments to check with the evidence and Approve and Reject
// (feature 25), orders waiting for money, money received with its signed receipt, and the
// one-time setup of SMS forwarding (feature 24), with the wallets read for the shop's country
// and the owner's yes to sharing the shape of messages no parser read (feature 73).
import { useCallback, useState } from "react";
import type { ChangeEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useCopy } from "../clipboard";
import type { Forwarding, OwnerMe, PaymentReview, Payments } from "../api";
import { isolate } from "../i18n";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";
import { formatMoney } from "../money";

const POLL_MS = 5000;
const DASH = "—";

/** Polls the payments panel, for people who handle payments; refresh() after a decision. */
export function usePayments(token: string, enabled: boolean) {
  const load = useCallback(() => api.payments(token), [token]);
  return usePolled(enabled ? load : null, POLL_MS);
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
                <span>{t("receipt.order", { code: isolate(review.order_code) })}</span>
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

function Wallets({
  token,
  data,
  onChange,
}: {
  token: string;
  data: Payments;
  onChange: () => void;
}) {
  const { t } = useLang();
  const [failed, setFailed] = useState(false);

  async function share(event: ChangeEvent<HTMLInputElement>) {
    setFailed(false);
    try {
      await api.shareUnreadSms(token, event.target.checked);
      onChange();
    } catch {
      setFailed(true);
    }
  }

  return (
    <>
      {data.wallets.length > 0 && (
        <ul className="pay-wallets" aria-label={t("console.sms_wallets")}>
          {data.wallets.map((wallet) => (
            <li key={wallet.name}>
              <span dir="auto">{wallet.name}</span>
              <span className="pay-note">
                {!wallet.read
                  ? t("console.sms_wallet_unread")
                  : wallet.checked
                    ? t("console.sms_wallet_checked")
                    : t("console.sms_wallet_unchecked")}
              </span>
            </li>
          ))}
        </ul>
      )}
      <label className="pay-share">
        <input type="checkbox" checked={data.share_unread} onChange={(e) => void share(e)} />
        {t("console.sms_share")}
      </label>
      {data.share_unread && (
        <p className="pay-note">{t("console.sms_share_kept", { count: data.unread_kept })}</p>
      )}
      {failed && <p role="alert">{t("console.offers_failed")}</p>}
    </>
  );
}

function SmsForwarding({
  token,
  data,
  onChange,
}: {
  token: string;
  data: Payments;
  onChange: () => void;
}) {
  const on = data.forwarding;
  const { t } = useLang();
  const [made, setMade] = useState<Forwarding | null>(null);
  const { copied, copy, reset } = useCopy();

  async function makeLink() {
    try {
      setMade(await api.setUpForwarding(token));
      reset();
      onChange();
    } catch {
      // The button stays; the owner can try again.
    }
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
      <Wallets token={token} data={data} onChange={onChange} />
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
        <SmsForwarding token={token} data={data} onChange={onChange} />
      </div>
    </section>
  );
}
