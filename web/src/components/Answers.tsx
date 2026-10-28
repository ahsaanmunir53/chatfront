// Answers the owner gave in a chat and then approved for the assistant to give again
// (feature 91). Removing one stops it at once.
import { useCallback } from "react";
import { api } from "../api";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 30_000;

export function AnswersPanel({ token }: { token: string }) {
  const { t } = useLang();
  const load = useCallback(() => api.answers(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  if (!data || data.length === 0) return null;
  return (
    <section className="pay-panel answers" aria-labelledby="answers-title">
      <h2 id="answers-title" className="section-title">
        {t("console.answers_title")}
      </h2>
      <p className="aside">{t("console.answers_lede")}</p>
      <ul className="answer-list">
        {data.map((item) => (
          <li key={item.id} className="pay-block answer-item">
            <p className="answer-q" dir="auto">
              <span>{t("console.answer_q")}</span> {item.question}
            </p>
            <p className="answer-a" dir="auto">
              <span>{t("console.answer_a")}</span> {item.answer}
            </p>
            <button
              type="button"
              className="action offer-stop"
              onClick={() => void api.removeAnswer(token, item.id).then(refresh, refresh)}
            >
              {t("console.answer_remove")}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
