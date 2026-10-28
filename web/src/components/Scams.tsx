// Scams (features 39 and 47): a call recording or voice note heard for the usual tricks, the
// shop's reports of scammers' numbers, accounts and links, and, for Chatfront's own staff,
// their review and the STIX report for NCCIA and CERT. Customers are never reported here;
// they go through the hashed fraud list.
import { useCallback, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { ApiError, api } from "../api";
import type { CallCheck, ScamKind } from "../api";
import { saveFile } from "../download";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 60_000;
const KINDS: ScamKind[] = ["phone", "wallet", "url"];
const POSING = [
  "courier",
  "jazzcash",
  "easypaisa",
  "bank",
  "fia",
  "police",
  "meta",
  "government",
  "other",
];

function CallCheckForm({ token }: { token: string }) {
  const { t } = useLang();
  const [state, setState] = useState<"idle" | "listening" | "failed" | "not_audio">("idle");
  const [found, setFound] = useState<CallCheck | null>(null);
  async function send(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setState("listening");
    setFound(null);
    try {
      setFound(await api.checkCall(token, file));
      setState("idle");
    } catch (error) {
      setState(error instanceof ApiError && error.status === 415 ? "not_audio" : "failed");
    }
  }
  return (
    <div className="pay-block">
      <h3 className="pay-block-title">{t("console.guardian_title")}</h3>
      <p className="aside">{t("console.guardian_lede")}</p>
      <label className="action action-primary" aria-busy={state === "listening"}>
        {state === "listening" ? t("console.guardian_listening") : t("console.guardian_upload")}
        <input
          type="file"
          accept="audio/*,video/*"
          className="sr-only"
          disabled={state === "listening"}
          onChange={(event) => void send(event)}
        />
      </label>
      {state === "failed" && <p role="alert">{t("guardian.not_heard")}</p>}
      {state === "not_audio" && <p role="alert">{t("console.guardian_not_audio")}</p>}
      {found && (
        <div role="status" className={`guardian-verdict is-${found.verdict}`}>
          <p>
            <strong>
              {t(`guardian.${found.verdict}` as Key, {
                posing: t(`guardian.posing.${found.reading.posing_as}` as Key),
              })}
            </strong>
          </p>
          {found.reading.summary && (
            <p dir="auto">{t("guardian.heard", { summary: found.reading.summary })}</p>
          )}
          {found.verdict !== "no_signs" && (
            <ul className="pay-signals">
              <li>{t("guardian.rule_code")}</li>
              <li>{t("guardian.rule_call_back")}</li>
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function ReportForm({ token, onSaved }: { token: string; onSaved: () => void }) {
  const { t } = useLang();
  const [kind, setKind] = useState<ScamKind>("phone");
  const [value, setValue] = useState("");
  const [posing, setPosing] = useState("other");
  const [failed, setFailed] = useState(false);
  async function save(event: FormEvent) {
    event.preventDefault();
    setFailed(false);
    try {
      await api.reportScam(token, kind, value.trim(), posing);
      setValue("");
      onSaved();
    } catch {
      setFailed(true);
    }
  }
  return (
    <form className="books-form" onSubmit={(event) => void save(event)}>
      <h3 className="pay-block-title">{t("console.scam_report_title")}</h3>
      <label>
        <span>{t("console.scam_kind")}</span>
        <select value={kind} onChange={(e) => setKind(e.target.value as ScamKind)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {t(`console.scam_kind.${k}` as Key)}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>{t("console.scam_value")}</span>
        <input
          value={value}
          required
          minLength={3}
          maxLength={300}
          dir="ltr"
          onChange={(e) => setValue(e.target.value)}
        />
      </label>
      <label>
        <span>{t("console.scam_posing")}</span>
        <select value={posing} onChange={(e) => setPosing(e.target.value)}>
          {POSING.map((p) => (
            <option key={p} value={p}>
              {t(`guardian.posing.${p}` as Key)}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" className="button button-ink button-small">
        {t("console.scam_report")}
      </button>
      {failed && <span role="alert">{t("console.offers_failed")}</span>}
    </form>
  );
}

export function ScamsPanel({ token }: { token: string }) {
  const { t } = useLang();
  const load = useCallback(() => api.scams(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  if (!data) return null;
  return (
    <section className="pay-panel scams" aria-labelledby="scams-title">
      <h2 id="scams-title" className="section-title">
        {t("console.scams_title")}
      </h2>
      <p className="aside">{t("console.scams_lede")}</p>
      <div className="pay-columns">
        <CallCheckForm token={token} />
        <div className="pay-block">
          <ReportForm token={token} onSaved={refresh} />
          {data.length > 0 && (
            <ul className="pay-rows">
              {data.map((report) => (
                <li key={report.id}>
                  <span>{t(`console.scam_kind.${report.kind}` as Key)}</span>
                  <span dir="ltr">{report.value}</span>
                  <span className="pay-note">
                    {t(`console.scam_status.${report.status}` as Key)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}

/** Chatfront's own review of the scams shops reported, and the STIX report. */
export function ScamReviewPanel({ token }: { token: string }) {
  const { t } = useLang();
  const load = useCallback(() => api.scamsToReview(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [failed, setFailed] = useState(false);
  async function report() {
    setFailed(false);
    try {
      saveFile(await api.stixReport(token), "chatfront-scams.json");
    } catch {
      setFailed(true);
    }
  }
  if (!data) return null;
  return (
    <section className="pay-panel scam-review" aria-labelledby="scam-review-title">
      <h2 id="scam-review-title" className="section-title">
        {t("console.scam_review_title")}
        <span className="console-count">{data.length}</span>
      </h2>
      <p className="aside">{t("console.scam_review_lede")}</p>
      <button type="button" className="action" onClick={() => void report()}>
        {t("console.stix_download")}
      </button>
      {failed && <p role="alert">{t("console.offers_failed")}</p>}
      {data.length === 0 ? (
        <p className="aside">{t("console.scam_review_none")}</p>
      ) : (
        <ul className="pay-rows">
          {data.map((item) => (
            <li key={item.id} className="fraud-case">
              <span className="khata-who" dir="auto">
                {item.shop}
              </span>
              <span>{t(`console.scam_kind.${item.kind}` as Key)}</span>
              <span dir="ltr">{item.value}</span>
              <span className="pay-note">{t(`guardian.posing.${item.posing_as}` as Key)}</span>
              <button
                type="button"
                className="action action-primary"
                onClick={() => void api.decideScam(token, item.id, true).then(refresh, refresh)}
              >
                {t("console.scam_confirm")}
              </button>
              <button
                type="button"
                className="action"
                onClick={() => void api.decideScam(token, item.id, false).then(refresh, refresh)}
              >
                {t("console.scam_dismiss")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
