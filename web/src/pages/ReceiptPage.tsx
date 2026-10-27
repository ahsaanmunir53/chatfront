// A signed receipt anyone can check (feature 26): scanning its QR code opens this page, which
// checks the shop's Ed25519 signature in the browser and shows what was paid.
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, api } from "../api";
import type { ReceiptCheck } from "../api";
import { Page, Problem } from "../components/Shell";
import { API_BASE } from "../config";
import { isolate, scriptLang } from "../i18n";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { verify } from "../receipt";
import type { Verdict } from "../receipt";
import { useServer } from "../server";

type Checked = Verdict | "checking";

function useReceipt(code: string) {
  const { state } = useServer();
  const [receipt, setReceipt] = useState<ReceiptCheck | null>(null);
  const [missing, setMissing] = useState(false);
  const [verdict, setVerdict] = useState<Checked>("checking");

  useEffect(() => {
    if (state !== "online" || !code) return;
    let current = true;
    api.receipt(code).then(
      async (found) => {
        if (!current) return;
        setReceipt(found);
        const result = await verify(found.payload, found.signature, found.public_key);
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
  return { receipt, missing, verdict };
}

function Verdict({ verdict, receipt }: { verdict: Checked; receipt: ReceiptCheck }) {
  const { t } = useLang();
  const shop = receipt.payload.shop;
  if (verdict === "checking") {
    return <p className="slip-verdict is-checking">{t("receipt.checking")}</p>;
  }
  const valid = verdict === "valid" || (verdict === "unsupported" && receipt.valid);
  return (
    <div className={`slip-verdict ${valid ? "is-valid" : "is-invalid"}`} role="status">
      <p>{valid ? t("receipt.valid", { shop }) : t("receipt.invalid")}</p>
      {verdict === "unsupported" && <p className="slip-small">{t("receipt.server_checked")}</p>}
    </div>
  );
}

export function ReceiptPage() {
  const { code = "" } = useParams();
  const { t, lang } = useLang();
  const { receipt, missing, verdict } = useReceipt(code);

  if (!code) {
    return (
      <Page>
        <section className="plain-hero">
          <h1 className="title">{t("receipt.title")}</h1>
          <p className="lede">{t("receipt.lede")}</p>
        </section>
      </Page>
    );
  }
  if (missing) {
    return (
      <Page>
        <Problem message={t("receipt.not_found")} />
      </Page>
    );
  }
  if (!receipt) {
    return (
      <Page>
        <div className="slip slip-skeleton" aria-busy="true" />
      </Page>
    );
  }

  const p = receipt.payload;
  const money = (minor: number) => formatMoney(minor, p.country, lang);
  const issued = new Date(p.issued_at).toLocaleString(lang, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  const valid = verdict === "valid" || (verdict === "unsupported" && receipt.valid);
  const method: Key = p.method === "cod" ? "receipt.method_cod" : "receipt.method_transfer";
  const by: Key = p.verified_by === "sms" ? "receipt.verified_sms" : "receipt.verified_owner";

  return (
    <Page>
      <Verdict verdict={verdict} receipt={receipt} />
      <article className="slip" aria-labelledby="slip-shop">
        <header className="slip-head">
          <div>
            <h1 id="slip-shop" className="slip-shop" lang={scriptLang(p.shop)} dir="auto">
              {p.shop}
            </h1>
            <p className="slip-order">{t("receipt.order", { code: isolate(p.order) })}</p>
          </div>
          {valid && (
            <span className="stamp slip-stamp" aria-hidden="true">
              ✓ {p.receipt}
            </span>
          )}
        </header>
        <p className="slip-paid">
          <span>{t("receipt.amount")}</span>
          <strong>{money(p.amount)}</strong>
        </p>
        {p.items.length > 0 && (
          <section className="slip-items" aria-label={t("receipt.items")}>
            <ul>
              {p.items.map((item) => (
                <li key={item.name} lang={scriptLang(item.name)} dir="auto">
                  <span>{item.name}</span>
                  <span>× {item.quantity}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        <dl className="slip-lines">
          <div>
            <dt>{t("receipt.total")}</dt>
            <dd>{money(p.order_total)}</dd>
          </div>
          <div>
            <dt>{t("receipt.paid_so_far")}</dt>
            <dd>{money(p.paid_so_far)}</dd>
          </div>
          <div>
            <dt>{t("receipt.balance")}</dt>
            <dd>{money(p.balance)}</dd>
          </div>
          <div>
            <dt>{t(method)}</dt>
            <dd>{t(by)}</dd>
          </div>
          {p.transaction_id && (
            <div>
              <dt>{t("receipt.tid")}</dt>
              <dd dir="ltr">{p.transaction_id}</dd>
            </div>
          )}
          <div>
            <dt>{t("receipt.issued")}</dt>
            <dd>{issued}</dd>
          </div>
        </dl>
        <footer className="slip-foot">
          <img
            className="slip-qr"
            src={`${API_BASE}/api/receipts/${encodeURIComponent(p.receipt)}/qr.svg`}
            alt=""
            width={132}
            height={132}
          />
          <details className="slip-proof">
            <summary>{t("receipt.key")}</summary>
            <code dir="ltr">{receipt.public_key}</code>
            <p>{t("receipt.signed_text")}</p>
            <code dir="ltr">{receipt.signed_text}</code>
          </details>
          <button type="button" className="action slip-print" onClick={() => window.print()}>
            {t("receipt.print")}
          </button>
        </footer>
      </article>
    </Page>
  );
}
