// The shop's customers (feature 59): what each has bought and is worth, who came back and who
// went quiet, greetings for the buyers whose chat is open, the verified-buyer badge (feature
// 38) and fraud reports (feature 37).
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type { CustomerSummary, OwnerCustomer, OwnerMe, Segment } from "../api";
import type { Key } from "../i18n";
import { FraudBadges, ReportFraud } from "./Fraud";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { useServer } from "../server";

const FILTERS: (Segment | "all")[] = ["all", "new", "repeat", "loyal", "quiet"];

/** A link that opens the owner's own WhatsApp with the greeting written, for buyers whose
 * chat is closed: the owner sends it themselves. */
export function whatsappLink(phone: string, text: string): string {
  return `https://wa.me/${phone.replace(/\D/g, "")}?${new URLSearchParams({ text })}`;
}

function Greeting({
  token,
  open,
  segment,
  text,
  onText,
}: {
  token: string;
  open: number; // buyers in the list shown whose chat is open
  segment: Segment | "all";
  text: string;
  onText: (text: string) => void;
}) {
  const { t } = useLang();
  const [result, setResult] = useState<{ sent: number; closed: number; recent: number } | null>(
    null,
  );
  const [failed, setFailed] = useState(false);
  async function send(event: FormEvent) {
    event.preventDefault();
    try {
      setResult(await api.greet(token, text.trim(), segment));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }
  return (
    <details className="greeting">
      <summary>{t("crm.greet_title")}</summary>
      <form className="books-form" onSubmit={(event) => void send(event)}>
        <label>
          {t("crm.greet_text")}
          <textarea
            value={text}
            rows={3}
            maxLength={500}
            dir="auto"
            onChange={(e) => onText(e.target.value)}
          />
        </label>
        <p className="pay-note">
          {open > 0 ? t("crm.greet_note", { count: open }) : t("crm.greet_none_open")}
        </p>
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={open === 0 || text.trim().length < 2}
        >
          {t("crm.greet_send")}
        </button>
        {result && (
          <p role="status">
            {t("crm.greet_done", { sent: result.sent })}
            {result.closed > 0 && ` ${t("crm.greet_closed", { count: result.closed })}`}
          </p>
        )}
        {failed && <p role="alert">{t("crm.greet_failed")}</p>}
      </form>
    </details>
  );
}

function Stats({ me, summary }: { me: OwnerMe; summary: CustomerSummary }) {
  const { t, lang } = useLang();
  if (summary.buyers === 0) return null;
  const lines = [
    t("crm.buyers", { count: summary.buyers }),
    summary.repeat_rate === null ? null : t("crm.came_back", { rate: summary.repeat_rate }),
    t("crm.average", { amount: formatMoney(summary.average_spent, me.country, lang) }),
    summary.quiet > 0 ? t("crm.quiet", { count: summary.quiet }) : null,
  ].filter((line): line is string => line !== null);
  return (
    <ul className="crm-stats">
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}

function CustomerRow({
  me,
  token,
  person,
  greeting,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  person: OwnerCustomer;
  greeting: string;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  const canTrust = me.rights.includes("trust");
  async function toggle() {
    await api.setVerified(token, person.id, !person.verified).catch(() => undefined);
    onChange();
  }
  const last = person.last_order
    ? new Intl.DateTimeFormat(lang, { dateStyle: "medium" }).format(new Date(person.last_order))
    : null;
  return (
    <li>
      <span dir="auto" className="khata-who">
        {person.name || t("console.customer_label")}
      </span>
      {person.segment !== "none" && (
        <span className={`segment is-${person.segment}`}>
          {t(`crm.segment_${person.segment}` as Key)}
        </span>
      )}
      {person.verified && (
        <span className="verified-badge">
          {t("console.customers_verified")}
          <span className="pay-note">
            {person.verified_by === "owner"
              ? t("console.customers_by_owner")
              : t("console.customers_by_rule")}
          </span>
        </span>
      )}
      <span className="pay-amount">{formatMoney(person.spent, me.country, lang)}</span>
      <span className="pay-note">
        {t("console.customers_counts", {
          orders: person.orders,
          delivered: person.delivered,
          returned: person.returned,
        })}
      </span>
      {last && <span className="pay-note">{t("crm.last_order", { date: last })}</span>}
      <FraudBadges person={person} />
      {person.owes > 0 && (
        <span className="pay-note">
          {t("console.customers_owes", { amount: formatMoney(person.owes, me.country, lang) })}
        </span>
      )}
      {person.phone && !person.open_chat && greeting.trim() && (
        <a
          className="action"
          href={whatsappLink(person.phone, greeting.trim())}
          target="_blank"
          rel="noreferrer"
        >
          {t("crm.greet_own")}
        </a>
      )}
      {canTrust && (
        <button type="button" className="action" onClick={() => void toggle()}>
          {person.verified ? t("console.customers_unverify") : t("console.customers_verify")}
        </button>
      )}
      {canTrust && <ReportFraud token={token} person={person} onDone={onChange} />}
    </li>
  );
}

export function CustomersPanel({ me, token }: { me: OwnerMe; token: string }) {
  const { t } = useLang();
  const { state } = useServer();
  const [people, setPeople] = useState<OwnerCustomer[] | null>(null);
  const [summary, setSummary] = useState<CustomerSummary | null>(null);
  const [filter, setFilter] = useState<Segment | "all">("all");
  const [greeting, setGreeting] = useState<string | null>(null);
  const canGreet = me.rights.includes("chats");
  const load = useCallback(() => {
    api.customers(token).then(setPeople, () => {
      // Shown again on the next change.
    });
    api.customerSummary(token).then(setSummary, () => undefined);
  }, [token]);
  useEffect(() => {
    if (state === "online") load();
  }, [state, load]);

  if (!people) return null;
  const shown = filter === "all" ? people : people.filter((p) => p.segment === filter);
  const text = greeting ?? summary?.greeting ?? "";
  return (
    <section className="customers-panel" aria-labelledby="customers-title">
      <h2 id="customers-title" className="section-title">
        {t("console.customers_title")}
        <span className="console-count">{people.length}</span>
      </h2>
      {summary && <Stats me={me} summary={summary} />}
      {people.length === 0 ? (
        <p className="aside">{t("console.customers_none")}</p>
      ) : (
        <>
          <div className="segment-filter" role="group" aria-label={t("crm.filter")}>
            {FILTERS.map((name) => (
              <button
                key={name}
                type="button"
                className="chip"
                aria-pressed={filter === name}
                onClick={() => setFilter(name)}
              >
                {t(`crm.filter_${name}` as Key)}
              </button>
            ))}
          </div>
          {canGreet && summary && (
            <Greeting
              token={token}
              open={shown.filter((person) => person.open_chat).length}
              segment={filter}
              text={text}
              onText={setGreeting}
            />
          )}
          <div className="pay-block">
            <ul className="pay-rows customers">
              {shown.map((person) => (
                <CustomerRow
                  key={person.id}
                  me={me}
                  token={token}
                  person={person}
                  greeting={canGreet ? text : ""}
                  onChange={load}
                />
              ))}
            </ul>
          </div>
        </>
      )}
    </section>
  );
}
