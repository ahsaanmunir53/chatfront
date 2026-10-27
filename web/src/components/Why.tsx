// The why panel (feature 107): for one order, each decision code made with the evidence it saw
// and the rule that fired, loaded when the owner opens it.
import { useState } from "react";
import { api } from "../api";
import type { OwnerMe, Why, WhyDecision } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";

type Money = (minor: unknown) => string;

function Decision({ decision, money }: { decision: WhyDecision; money: Money }) {
  const { t } = useLang();
  const v = decision.values;
  if (decision.topic === "risk") {
    return (
      <li>
        <strong>{t(`risk.${decision.outcome}` as Key)}</strong>{" "}
        {t("why.risk_score", { score: Number(v.score ?? 0) })}
        <ul>
          {decision.evidence.map((reason) => (
            <li key={String(reason.code)}>
              {t(`risk.${String(reason.code)}` as Key, { count: Number(reason.count ?? 0) })}{" "}
              <span className="pay-note">{t("why.points", { points: Number(reason.points) })}</span>
            </li>
          ))}
        </ul>
      </li>
    );
  }
  if (decision.topic === "advance") {
    return <li>{t(`why.advance.${decision.rule}` as Key, { advance: money(v.advance) })}</li>;
  }
  if (decision.topic === "price") {
    return (
      <li>
        {t("why.price", { name: String(v.name), agreed: money(v.agreed), list: money(v.list) })}
        <ul>
          {decision.evidence.map((round) => (
            <li key={String(round.round)}>
              {t("why.offer", {
                round: Number(round.round),
                offered: money(round.offered),
                answer: money(round.answer),
              })}{" "}
              <span className="pay-note">{t(`why.outcome.${String(round.outcome)}` as Key)}</span>
            </li>
          ))}
        </ul>
      </li>
    );
  }
  return (
    <li>
      {t(`why.payment.${decision.rule}` as Key, {
        amount: money(v.amount ?? v.sms_amount),
      })}
      {decision.outcome !== "settled" && (
        <span className="pay-note"> {t(`why.claim.${decision.outcome}` as Key)}</span>
      )}
      {decision.evidence.length > 0 && (
        <ul>
          {decision.evidence.map((signal) => (
            <li key={String(signal.code)} dir="auto">
              {t(`payment.signal.${String(signal.code)}` as Key, {
                detail: String(signal.detail ?? ""),
              })}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function WhyDetails({ me, token, code }: { me: OwnerMe; token: string; code: string }) {
  const { t, lang } = useLang();
  const [why, setWhy] = useState<Why | null>(null);
  const [failed, setFailed] = useState(false);
  const money: Money = (minor) =>
    typeof minor === "number" ? formatMoney(minor, me.country, lang) : "—";

  function open(event: React.SyntheticEvent<HTMLDetailsElement>) {
    if (!event.currentTarget.open || why) return;
    api.why(token, code).then(setWhy, () => setFailed(true));
  }

  return (
    <details className="order-why" onToggle={open}>
      <summary>{t("console.why")}</summary>
      {failed && <p role="alert">{t("console.move_failed")}</p>}
      {why && (
        <>
          <ul className="why-decisions">
            {why.decisions.map((decision, index) => (
              <Decision key={`${decision.topic}-${index}`} decision={decision} money={money} />
            ))}
          </ul>
          <h4 className="why-history-title">{t("why.history")}</h4>
          <ol className="why-history">
            {why.history.map((step) => (
              <li key={step.at}>
                <time dateTime={step.at}>
                  {new Date(step.at).toLocaleString(lang, {
                    day: "numeric",
                    month: "short",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </time>{" "}
                {step.action === "order.status"
                  ? t(`order.status.${String(step.data.to)}` as Key)
                  : t(`activity.${step.action.replace(".", "_")}` as Key)}
              </li>
            ))}
          </ol>
        </>
      )}
    </details>
  );
}
