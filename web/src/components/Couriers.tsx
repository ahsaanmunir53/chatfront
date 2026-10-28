// Couriers (features 31, 32 and 35): the shop's own courier account, a packed parcel booked
// with it from the order, the courier's cash-on-delivery sheet matched to the orders (money
// short or never paid stands out), and the rider's photo of a delivery read for the order's
// code. A photo never moves an order on; the owner does.
import { useCallback, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { ApiError, api } from "../api";
import type {
  CourierAccount,
  DeliveryProof,
  OwnerMe,
  OwnerOrder,
  SettlementReport,
  Settlements,
} from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { usePolled } from "../usePolled";

const ACCOUNTS_POLL_MS = 300_000;
const SETTLEMENTS_POLL_MS = 60_000;
const SHEETS =
  ".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const COURIER_COUNTRIES = new Set(["PK"]);
const PROOF_STATES = new Set(["dispatched", "delivered"]);

/** The shop's courier accounts, for booking from an order row; none without the right. */
export function useCouriers(token: string, enabled: boolean): CourierAccount[] {
  const load = useCallback(() => api.couriers(token), [token]);
  const { data } = usePolled(enabled ? load : null, ACCOUNTS_POLL_MS);
  return (data ?? []).filter((account) => account.connected);
}

function errorText(error: unknown, t: ReturnType<typeof useLang>["t"]): string {
  const known = ["not_bookable", "booked_not_moved", "courier_bad_response", "upstream_error"];
  if (error instanceof ApiError && known.includes(error.code)) {
    return t(`console.err.${error.code}` as Key);
  }
  return t("console.move_failed");
}

/** "Book with PostEx" on a packed order: the courier's tracking number comes back. */
export function BookParcel({
  token,
  order,
  couriers,
  onDone,
}: {
  token: string;
  order: OwnerOrder;
  couriers: CourierAccount[];
  onDone: () => void;
}) {
  const { t } = useLang();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  if (order.status !== "packed" || couriers.length === 0) return null;
  async function book(courier: string) {
    setBusy(true);
    setFailed(null);
    try {
      await api.bookParcel(token, order.code, courier);
      onDone();
    } catch (error) {
      setFailed(errorText(error, t));
    }
    setBusy(false);
  }
  return (
    <>
      {couriers.map((account) => (
        <button
          key={account.courier}
          type="button"
          className="action"
          disabled={busy}
          onClick={() => void book(account.courier)}
        >
          {t("console.courier_book", { courier: account.name })}
        </button>
      ))}
      {failed && (
        <p className="order-failed" role="alert">
          {failed}
        </p>
      )}
    </>
  );
}

function ProofItem({ proof }: { proof: DeliveryProof }) {
  const { t, lang } = useLang();
  const verdict = proof.matched === null ? "reading" : proof.matched ? "matched" : "not_matched";
  return (
    <li className={`proof-item is-${verdict}`}>
      <a className="pay-shot" href={proof.url} target="_blank" rel="noopener noreferrer">
        <img src={proof.url} alt={t("console.proof_photo")} loading="lazy" />
      </a>
      <p>
        <span>{t(`console.proof.${verdict}` as Key)}</span>{" "}
        <time className="pay-note" dateTime={proof.at}>
          {new Date(proof.at).toLocaleString(lang, {
            day: "numeric",
            month: "short",
            hour: "numeric",
            minute: "2-digit",
          })}
        </time>
      </p>
      {proof.reading?.damage_seen && <p role="alert">{t("console.proof_damage")}</p>}
      {proof.reading?.notes && (
        <p className="pay-note" dir="auto">
          {proof.reading.notes}
        </p>
      )}
    </li>
  );
}

/** The rider's photos of a parcel on its way or delivered, opened on demand (feature 35). */
export function DeliveryProofs({ token, order }: { token: string; order: OwnerOrder }) {
  const { t } = useLang();
  const [proofs, setProofs] = useState<DeliveryProof[] | null>(null);
  const [state, setState] = useState<"idle" | "sending" | "failed">("idle");
  if (!PROOF_STATES.has(order.status)) return null;
  async function load() {
    setProofs(await api.deliveryProofs(token, order.code).catch(() => []));
  }
  async function send(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setState("sending");
    try {
      const added = await api.addDeliveryProof(token, order.code, file);
      setProofs([added, ...(proofs ?? [])]);
      setState("idle");
    } catch {
      setState("failed");
    }
  }
  return (
    <details
      className="delivery-proofs"
      onToggle={(event) => {
        if ((event.target as HTMLDetailsElement).open && proofs === null) void load();
      }}
    >
      <summary>{t("console.proof_title")}</summary>
      <label className="action packing-photo" aria-busy={state === "sending"}>
        {state === "sending" ? t("console.proof_sending") : t("console.proof_add")}
        <input
          type="file"
          accept="image/*"
          capture="environment"
          className="sr-only"
          disabled={state === "sending"}
          onChange={(event) => void send(event)}
        />
      </label>
      {state === "failed" && (
        <p className="order-failed" role="alert">
          {t("console.proof_failed")}
        </p>
      )}
      {proofs !== null && proofs.length === 0 && <p className="aside">{t("console.proof_none")}</p>}
      {proofs && proofs.length > 0 && (
        <ul className="proof-list">
          {proofs.map((proof) => (
            <ProofItem key={proof.id} proof={proof} />
          ))}
        </ul>
      )}
    </details>
  );
}

function ConnectForm({
  token,
  account,
  onSaved,
}: {
  token: string;
  account: CourierAccount;
  onSaved: () => void;
}) {
  const { t } = useLang();
  const [secret, setSecret] = useState("");
  const [pickup, setPickup] = useState(account.pickup_code ?? "");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  async function save(event: FormEvent) {
    event.preventDefault();
    setState("saving");
    try {
      await api.connectCourier(token, account.courier, secret.trim(), pickup.trim());
      setSecret("");
      setState("saved");
      onSaved();
    } catch {
      setState("failed");
    }
  }
  return (
    <form className="books-form courier-form" onSubmit={(event) => void save(event)}>
      <h3 className="pay-block-title">
        {account.name}{" "}
        <span className={account.connected ? "pay-on" : "pay-note"}>
          {account.connected ? t("console.courier_connected") : t("console.courier_not_connected")}
        </span>
      </h3>
      <label>
        <span>
          {account.connected
            ? t("console.courier_token_replace", { courier: account.name })
            : t("console.courier_token", { courier: account.name })}
        </span>
        <input
          type="password"
          value={secret}
          required
          minLength={8}
          maxLength={400}
          autoComplete="off"
          dir="ltr"
          onChange={(e) => setSecret(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.courier_pickup")}</span>
        <input
          value={pickup}
          maxLength={40}
          dir="ltr"
          onChange={(e) => setPickup(e.target.value)}
        />
      </label>
      <p className="aside">{t("console.courier_token_note", { courier: account.name })}</p>
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving"}
        >
          {t("console.courier_connect")}
        </button>
        {state === "saved" && <span role="status">{t("console.saved")}</span>}
        {state === "failed" && <span role="alert">{t("console.offers_failed")}</span>}
      </div>
    </form>
  );
}

function ReportLines({ report }: { report: SettlementReport }) {
  const { t } = useLang();
  const groups: [Key, string[]][] = [
    ["console.settle_short", report.short],
    ["console.settle_unknown", report.unknown],
    ["console.settle_paid_before", report.paid_before],
    ["console.settle_delivered", report.delivered],
    ["console.settle_returned", report.returned],
  ];
  return (
    <ul className="pay-signals settle-report">
      {groups
        .filter(([, codes]) => codes.length > 0)
        .map(([label, codes]) => (
          <li key={label}>
            {t(label, { count: codes.length })} <span dir="ltr">{codes.join(", ")}</span>
          </li>
        ))}
    </ul>
  );
}

function SettlementUpload({
  me,
  token,
  onDone,
}: {
  me: OwnerMe;
  token: string;
  onDone: () => void;
}) {
  const { t, lang } = useLang();
  const [report, setReport] = useState<SettlementReport | null>(null);
  const [state, setState] = useState<"idle" | "reading" | string>("idle");
  const money = (minor: number) => formatMoney(minor, me.country, lang);
  async function send(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setState("reading");
    try {
      setReport(await api.uploadSettlement(token, file));
      setState("idle");
      onDone();
    } catch (error) {
      setState(error instanceof ApiError ? error.code : "failed");
    }
  }
  const known = ["empty_sheet", "no_tracking_column", "sheet_too_big", "bad_sheet"];
  return (
    <div className="settle-upload">
      <h3 className="pay-block-title">{t("console.settle_title")}</h3>
      <p className="aside">{t("console.settle_note")}</p>
      <label className="action action-primary" aria-busy={state === "reading"}>
        {state === "reading" ? t("console.settle_reading") : t("console.settle_upload")}
        <input
          type="file"
          accept={SHEETS}
          className="sr-only"
          disabled={state === "reading"}
          onChange={(event) => void send(event)}
        />
      </label>
      {state !== "idle" && state !== "reading" && (
        <p className="order-failed" role="alert">
          {t(known.includes(state) ? (`console.err.${state}` as Key) : "console.offers_failed")}
        </p>
      )}
      {report && (
        <div role="status">
          <p>
            {t("console.settle_matched", { matched: report.matched, rows: report.rows })}{" "}
            {t("console.settle_money", {
              collected: money(report.collected),
              paid: money(report.paid_out),
            })}
          </p>
          <ReportLines report={report} />
        </div>
      )}
    </div>
  );
}

function SettlementList({ me, data }: { me: OwnerMe; data: Settlements }) {
  const { t, lang } = useLang();
  const money = (minor: number | null) =>
    minor === null ? "-" : formatMoney(minor, me.country, lang);
  if (data.rows.length === 0) return <p className="aside">{t("console.settle_none")}</p>;
  return (
    <>
      <p>
        {t("console.settle_totals", {
          paid: money(data.paid_total),
          short: money(data.short_total),
        })}
      </p>
      <table className="daybook">
        <thead>
          <tr>
            <th scope="col">{t("console.settle_order")}</th>
            <th scope="col">{t("console.settle_collected")}</th>
            <th scope="col">{t("console.settle_charges")}</th>
            <th scope="col">{t("console.settle_paid")}</th>
            <th scope="col">{t("console.settle_short_col")}</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => (
            <tr key={`${row.order}-${row.at}`} className={row.short > 0 ? "is-short" : undefined}>
              <th scope="row">{row.order}</th>
              <td>{money(row.collected)}</td>
              <td>{money(row.charges)}</td>
              <td>{money(row.paid)}</td>
              <td>{row.short > 0 ? money(row.short) : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function SettlementsBlock({ me, token }: { me: OwnerMe; token: string }) {
  const load = useCallback(() => api.settlements(token), [token]);
  const { data, refresh } = usePolled(load, SETTLEMENTS_POLL_MS);
  return (
    <div className="pay-block">
      <SettlementUpload me={me} token={token} onDone={refresh} />
      {data && <SettlementList me={me} data={data} />}
    </div>
  );
}

export function CouriersPanel({
  me,
  token,
  canConnect,
  canBooks,
}: {
  me: OwnerMe;
  token: string;
  canConnect: boolean;
  canBooks: boolean;
}) {
  const { t } = useLang();
  const load = useCallback(() => api.couriers(token), [token]);
  const shown = canConnect && COURIER_COUNTRIES.has(me.country);
  const { data, refresh } = usePolled(shown ? load : null, ACCOUNTS_POLL_MS);
  const accounts = shown ? (data ?? []) : [];
  if (accounts.length === 0 && !canBooks) return null;
  return (
    <section className="pay-panel couriers" aria-labelledby="couriers-title">
      <h2 id="couriers-title" className="section-title">
        {t("console.couriers_title")}
      </h2>
      <p className="aside">{t("console.couriers_lede")}</p>
      <div className="pay-columns">
        {accounts.length > 0 && (
          <div className="pay-block">
            {accounts.map((account) => (
              <ConnectForm
                key={account.courier}
                token={token}
                account={account}
                onSaved={refresh}
              />
            ))}
          </div>
        )}
        {canBooks && <SettlementsBlock me={me} token={token} />}
      </div>
    </section>
  );
}
