// The fraud-buyer list (feature 37): a shop reports a buyer from its customers; the report
// counts for that shop at once, and the number goes on the shared list, only as a hash, once
// Chatfront's own staff have reviewed it. Being on the list never blocks anyone: it raises the
// cash-on-delivery risk score, so the shop asks for an advance.
import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type { FraudReason, FraudStatus, FraudToReview, OwnerCustomer } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 30_000;
export const REASONS: FraudReason[] = ["fake_payment", "refused_cod", "scam", "abuse"];
export const REASON: Record<FraudReason, Key> = {
  fake_payment: "fraud.reason_fake_payment",
  refused_cod: "fraud.reason_refused_cod",
  scam: "fraud.reason_scam",
  abuse: "fraud.reason_abuse",
};
const STATUS: Record<FraudStatus, Key> = {
  pending: "fraud.status_pending",
  confirmed: "fraud.status_confirmed",
  rejected: "fraud.status_rejected",
  withdrawn: "fraud.status_withdrawn",
};

/** What the lists say about a customer, for the customers list. */
export function FraudBadges({ person }: { person: OwnerCustomer }) {
  const { t } = useLang();
  return (
    <>
      {person.reported && person.reported !== "withdrawn" && (
        <span className={`fraud-badge is-${person.reported}`}>{t(STATUS[person.reported])}</span>
      )}
      {person.listed_by > 0 && (
        <span className="fraud-badge is-listed">
          {t("fraud.listed", { count: person.listed_by })}
        </span>
      )}
    </>
  );
}

/** Report a customer: a reason and a note; code gathers the evidence. */
export function ReportFraud({
  token,
  person,
  onDone,
}: {
  token: string;
  person: OwnerCustomer;
  onDone: () => void;
}) {
  const { t } = useLang();
  const [reason, setReason] = useState<FraudReason>("fake_payment");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState(false);

  async function send(event: FormEvent) {
    event.preventDefault();
    try {
      await api.reportFraud(token, person.id, reason, note);
      onDone();
    } catch {
      setProblem(true);
    }
  }

  if (person.reported === "pending" || person.reported === "confirmed") return null;
  return (
    <details className="fraud-report">
      <summary>{t("fraud.report")}</summary>
      <form className="books-form" onSubmit={(event) => void send(event)}>
        <label>
          {t("fraud.reason")}
          <select value={reason} onChange={(e) => setReason(e.target.value as FraudReason)}>
            {REASONS.map((r) => (
              <option key={r} value={r}>
                {t(REASON[r])}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("fraud.note")}
          <input
            value={note}
            maxLength={500}
            dir="auto"
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
        <p className="pay-note">{t("fraud.report_note")}</p>
        <button type="submit" className="button button-ink button-small">
          {t("fraud.send")}
        </button>
        {problem && <p role="alert">{t("fraud.no_number")}</p>}
      </form>
    </details>
  );
}

export function FraudReportsPanel({ token }: { token: string }) {
  const { t } = useLang();
  const load = useCallback(() => api.fraudReports(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);

  async function withdraw(id: string) {
    await api.withdrawReport(token, id).catch(() => undefined);
    refresh();
  }

  if (!data || data.length === 0) return null;
  return (
    <section className="pay-panel" aria-labelledby="fraud-reports-title">
      <h2 id="fraud-reports-title" className="section-title">
        {t("fraud.reports_title")}
      </h2>
      <p className="aside">{t("fraud.reports_lede")}</p>
      <ul className="pay-rows">
        {data.map((report) => (
          <li key={report.id}>
            <span dir="auto" className="khata-who">
              {report.name || t("console.customer_label")}
            </span>
            <span>{t(REASON[report.reason])}</span>
            <span className={`fraud-badge is-${report.status}`}>{t(STATUS[report.status])}</span>
            {report.review_note && (
              <span className="pay-note" dir="auto">
                {report.review_note}
              </span>
            )}
            {(report.status === "pending" || report.status === "confirmed") && (
              <button type="button" className="action" onClick={() => void withdraw(report.id)}>
                {t("fraud.withdraw")}
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Evidence({ report }: { report: FraudToReview }) {
  const { t } = useLang();
  const orders = report.evidence.orders ?? [];
  return (
    <ul className="fraud-evidence">
      {orders.map((order) => (
        <li key={order.code}>
          <span className="pay-code">{order.code}</span> {order.status}
          {order.method ? `, ${order.method}` : ""}
        </li>
      ))}
      {(report.evidence.rejected_payments ?? 0) > 0 && (
        <li>{t("fraud.rejected_payments", { count: report.evidence.rejected_payments ?? 0 })}</li>
      )}
      {report.others > 0 && <li>{t("fraud.others", { count: report.others })}</li>}
    </ul>
  );
}

/** Chatfront's own staff review reports before a number is shared. */
export function FraudReviewPanel({ token }: { token: string }) {
  const { t } = useLang();
  const load = useCallback(() => api.fraudReview(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [notes, setNotes] = useState<Record<string, string>>({});

  async function decide(id: string, confirm: boolean) {
    await api.decideReport(token, id, confirm, notes[id] ?? "").catch(() => undefined);
    refresh();
  }

  async function unlist(id: string) {
    await api.unlist(token, id, notes[id] || t("fraud.unlisted_reason")).catch(() => undefined);
    refresh();
  }

  if (!data) return null;
  return (
    <section className="pay-panel fraud-review" aria-labelledby="fraud-review-title">
      <h2 id="fraud-review-title" className="section-title">
        {t("fraud.review_title")}
        <span className="console-count">{data.pending.length}</span>
      </h2>
      <p className="aside">{t("fraud.review_lede")}</p>
      {data.pending.length === 0 && <p className="aside">{t("fraud.review_none")}</p>}
      <ul className="pay-rows">
        {data.pending.map((report) => (
          <li key={report.id} className="fraud-case">
            <span className="khata-who" dir="auto">
              {report.shop}
            </span>
            <span>{t(REASON[report.reason])}</span>
            {report.note && (
              <span className="pay-note" dir="auto">
                {report.note}
              </span>
            )}
            <Evidence report={report} />
            <label className="fraud-review-note">
              {t("fraud.review_note")}
              <input
                value={notes[report.id] ?? ""}
                maxLength={300}
                onChange={(e) => setNotes((now) => ({ ...now, [report.id]: e.target.value }))}
              />
            </label>
            <span className="import-actions">
              <button
                type="button"
                className="button button-ink button-small"
                onClick={() => void decide(report.id, true)}
              >
                {t("fraud.confirm")}
              </button>
              <button
                type="button"
                className="action"
                onClick={() => void decide(report.id, false)}
              >
                {t("fraud.reject")}
              </button>
            </span>
          </li>
        ))}
      </ul>
      {data.listed.length > 0 && (
        <>
          <h3 className="pay-block-title">{t("fraud.list_title")}</h3>
          <ul className="pay-rows">
            {data.listed.map((entry) => (
              <li key={entry.id}>
                <span className="pay-code" dir="ltr">
                  {entry.ref}…
                </span>
                <span>{entry.reasons.map((r) => t(REASON[r])).join(", ")}</span>
                <span className="pay-note">{t("fraud.listed", { count: entry.shops })}</span>
                <button type="button" className="action" onClick={() => void unlist(entry.id)}>
                  {t("fraud.unlist")}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
