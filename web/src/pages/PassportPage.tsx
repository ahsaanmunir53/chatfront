// A credit passport anyone with its code can check (feature 64): the shop's sales history for
// the last twelve months, signed with the shop's own key. The page checks the signature in the
// browser, as the receipt page does, so a lender need not take the server's word for it.
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, api } from "../api";
import type { PublicPassport } from "../api";
import { Page, Problem } from "../components/Shell";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { verify } from "../receipt";
import type { Verdict } from "../receipt";
import { useServer } from "../server";

function Checked({
  verdict,
  passport,
}: {
  verdict: Verdict | "checking";
  passport: PublicPassport;
}) {
  const { t } = useLang();
  if (verdict === "checking")
    return <p className="slip-verdict is-checking">{t("receipt.checking")}</p>;
  const valid = verdict === "valid" || (verdict === "unsupported" && passport.valid);
  return (
    <div
      className={`slip-verdict ${valid && !passport.revoked ? "is-valid" : "is-invalid"}`}
      role="status"
    >
      <p>
        {passport.revoked
          ? t("passport.revoked")
          : valid
            ? t("passport.valid", { shop: passport.statement.shop })
            : t("receipt.invalid")}
      </p>
    </div>
  );
}

export function PassportPage() {
  const { code = "" } = useParams();
  const { t, lang } = useLang();
  const { state } = useServer();
  const [passport, setPassport] = useState<PublicPassport | null>(null);
  const [missing, setMissing] = useState(false);
  const [verdict, setVerdict] = useState<Verdict | "checking">("checking");

  useEffect(() => {
    if (state !== "online" || !code) return;
    let current = true;
    api.passport(code).then(
      async (found) => {
        if (!current) return;
        setPassport(found);
        const result = await verify(found.statement, found.signature, found.public_key);
        if (current) setVerdict(result);
      },
      (error: unknown) => {
        if (current && error instanceof ApiError && error.status === 404) setMissing(true);
      },
    );
    return () => {
      current = false;
    };
  }, [code, state]);

  if (missing) {
    return (
      <Page>
        <Problem message={t("passport.not_found")} />
      </Page>
    );
  }
  if (!passport) {
    return (
      <Page>
        <div className="profile-skeleton" aria-busy="true" />
      </Page>
    );
  }
  const s = passport.statement;
  const money = (minor: number) => formatMoney(minor, s.country, lang);
  return (
    <Page>
      <section className="plain-hero passport">
        <h1 className="title" dir="auto">
          {t("passport.title", { shop: s.shop })}
        </h1>
        <p className="lede">
          {t("passport.lede", {
            since: s.selling_since ?? "-",
            made: s.made_on,
            code: s.code,
          })}
        </p>
        <Checked verdict={verdict} passport={passport} />
      </section>
      <section className="plain-section" aria-labelledby="passport-months">
        <h2 id="passport-months" className="section-title">
          {t("passport.months")}
        </h2>
        <table className="daybook passport-table">
          <thead>
            <tr>
              <th scope="col">{t("passport.month")}</th>
              <th scope="col">{t("passport.delivered")}</th>
              <th scope="col">{t("passport.value")}</th>
              <th scope="col">{t("passport.sms")}</th>
              <th scope="col">{t("passport.owner")}</th>
              <th scope="col">{t("passport.cash")}</th>
              <th scope="col">{t("passport.returned")}</th>
            </tr>
          </thead>
          <tbody>
            {s.months.map((m) => (
              <tr key={m.month}>
                <th scope="row">{m.month}</th>
                <td>{m.orders_delivered}</td>
                <td>{money(m.delivered_value)}</td>
                <td>{money(m.paid_matched_to_sms)}</td>
                <td>{money(m.paid_approved_by_owner)}</td>
                <td>{money(m.cash_at_door)}</td>
                <td>{m.orders_returned}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row">{t("passport.total")}</th>
              <td>{s.totals.orders_delivered}</td>
              <td>{money(s.totals.delivered_value)}</td>
              <td>{money(s.totals.paid_matched_to_sms)}</td>
              <td>{money(s.totals.paid_approved_by_owner)}</td>
              <td>{money(s.totals.cash_at_door)}</td>
              <td>{s.totals.orders_returned}</td>
            </tr>
          </tfoot>
        </table>
        <p className="pay-note">{t("passport.note")}</p>
        <details className="slip-small">
          <summary>{t("passport.signed_text")}</summary>
          <code dir="ltr" className="signed-text">
            {passport.signed_text}
          </code>
          <p dir="ltr">
            {t("passport.key")} {passport.public_key}
          </p>
        </details>
      </section>
    </Page>
  );
}
