// Wallet SMS formats, for Chatfront's own staff (feature 73): every parser with the real money
// it read and whether it has passed on a real message, and the shapes of wallet messages no
// parser could read, from shops that agreed to share them. Digits show as 9 and names as NAME.
import { useCallback } from "react";
import { api } from "../api";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 60_000;

export function SmsFormatsPanel({ token }: { token: string }) {
  const { t } = useLang();
  const load = useCallback(() => api.smsFormats(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  if (!data) return null;
  return (
    <section className="pay-panel sms-formats" aria-labelledby="sms-formats-title">
      <h2 id="sms-formats-title" className="section-title">
        {t("console.sms_formats_title")}
      </h2>
      <p className="aside">{t("console.sms_formats_lede")}</p>
      <table className="sms-format-table">
        <thead>
          <tr>
            <th scope="col">{t("console.sms_formats_wallet")}</th>
            <th scope="col">{t("console.sms_formats_country")}</th>
            <th scope="col">{t("console.sms_formats_checked")}</th>
            <th scope="col">{t("console.sms_formats_read")}</th>
          </tr>
        </thead>
        <tbody>
          {data.parsers.map((parser) => (
            <tr key={parser.parser}>
              <th scope="row">{parser.provider}</th>
              <td>{parser.country}</td>
              <td>
                {parser.verified ? t("console.sms_formats_yes") : t("console.sms_formats_no")}
              </td>
              <td>{parser.credits}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3 className="pay-block-title">
        {t("console.sms_formats_unread")}
        <span className="console-count">{data.unread.length}</span>
      </h3>
      {data.unread.length === 0 ? (
        <p className="aside">{t("console.sms_formats_none")}</p>
      ) : (
        <ul className="pay-rows">
          {data.unread.map((sample) => (
            <li key={sample.id} className="sms-sample">
              <span>{sample.country}</span>
              <span dir="ltr">{sample.sender}</span>
              <code dir="auto">{sample.text}</code>
              <span className="pay-note">
                {t("console.sms_formats_seen", { count: sample.seen })}
              </span>
              <button
                type="button"
                className="action"
                onClick={() => void api.smsFormatHandled(token, sample.id).then(refresh, refresh)}
              >
                {t("console.sms_formats_handled")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
