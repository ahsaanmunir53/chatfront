// Stock counted from a shelf photo (feature 87): the model counts what it can see, code
// matches it to products, and the owner checks every number before it becomes the stock.
import { useState } from "react";
import type { ChangeEvent } from "react";
import { ApiError, api } from "../api";
import type { ShelfCount } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { shrinkImage } from "../media";

type Phase = "idle" | "reading" | "saving";

export function ShelfCountPanel({ token, onSaved }: { token: string; onSaved: () => void }) {
  const { t } = useLang();
  const [phase, setPhase] = useState<Phase>("idle");
  const [reading, setReading] = useState<ShelfCount | null>(null);
  const [counts, setCounts] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<{ key: Key; count?: number; alert?: boolean } | null>(
    null,
  );

  async function pick(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setPhase("reading");
    setMessage(null);
    try {
      const found = await api.countShelf(token, await shrinkImage(file));
      setReading(found);
      setCounts(Object.fromEntries(found.rows.map((row) => [row.variant_id, String(row.counted)])));
      if (found.rows.length === 0) setMessage({ key: "stock.count_nothing", alert: true });
    } catch (error) {
      const model = error instanceof ApiError && error.status === 503;
      setMessage({ key: model ? "stock.count_error_model" : "stock.count_error", alert: true });
    }
    setPhase("idle");
  }

  async function save() {
    if (!reading) return;
    const lines = reading.rows.map((row) => ({
      variant_id: row.variant_id,
      counted: Number(counts[row.variant_id]),
    }));
    if (lines.some((line) => !Number.isInteger(line.counted) || line.counted < 0)) {
      setMessage({ key: "stock.count_whole", alert: true });
      return;
    }
    setPhase("saving");
    try {
      const { changed } = await api.saveCount(token, lines);
      setReading(null);
      setMessage({ key: "stock.count_saved", count: changed });
      onSaved();
    } catch {
      setMessage({ key: "stock.count_error", alert: true });
    }
    setPhase("idle");
  }

  return (
    <section className="pay-panel shelf-count" aria-labelledby="shelf-count-title">
      <h2 id="shelf-count-title" className="section-title">
        {t("stock.count_title")}
      </h2>
      <p className="aside">{t("stock.count_lede")}</p>
      <label className="action import-pick">
        {t("stock.count_pick")}
        <input
          className="sr-only"
          type="file"
          accept="image/*"
          disabled={phase !== "idle"}
          onChange={(event) => void pick(event)}
        />
      </label>
      {phase === "reading" && <p role="status">{t("stock.count_reading")}</p>}
      {message && (
        <p role={message.alert ? "alert" : "status"} className="pay-note">
          {t(message.key, { count: message.count ?? 0 })}
        </p>
      )}
      {reading && reading.rows.length > 0 && (
        <div className="shelf-count-rows">
          <table>
            <thead>
              <tr>
                <th scope="col">{t("stock.count_col_name")}</th>
                <th scope="col">{t("stock.count_col_books")}</th>
                <th scope="col">{t("stock.count_col_counted")}</th>
              </tr>
            </thead>
            <tbody>
              {reading.rows.map((row) => (
                <tr key={row.variant_id}>
                  <th scope="row" dir="auto">
                    {row.name}
                  </th>
                  <td>{row.before ?? t("console.stock_open")}</td>
                  <td>
                    <input
                      aria-label={t("stock.count_for", { name: row.name })}
                      inputMode="numeric"
                      value={counts[row.variant_id] ?? ""}
                      onChange={(e) =>
                        setCounts((now) => ({ ...now, [row.variant_id]: e.target.value }))
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {reading.problems.length > 0 && (
            <ul className="pay-note">
              {reading.problems.map((problem) => (
                <li key={problem} dir="auto">
                  {problem}
                </li>
              ))}
            </ul>
          )}
          <p className="pay-note">{t("stock.count_loss_note")}</p>
          <button
            type="button"
            className="button button-ink button-small"
            disabled={phase !== "idle"}
            onClick={() => void save()}
          >
            {t("stock.count_save")}
          </button>
        </div>
      )}
    </section>
  );
}
