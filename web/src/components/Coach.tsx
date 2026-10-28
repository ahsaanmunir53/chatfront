// The coach's weekly note (feature 65) and the seasons ahead (feature 55). Code finds the facts
// and writes every number; the model, when there is one, adds advice in words only.
import { useCallback, useState } from "react";
import { api } from "../api";
import type { CoachNote, SeasonAhead } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 300_000;

function Season({ season }: { season: SeasonAhead }) {
  const { t, lang } = useLang();
  const day = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" });
  return (
    <li className="season-row">
      <strong>{t(`season.${season.key}` as Key)}</strong>{" "}
      <span className="pay-note">
        {t("console.season_dates", { starts: day(season.starts), ends: day(season.ends) })}
      </span>
      {season.lifts.length > 0 ? (
        <ul className="season-lifts">
          {season.lifts.map((lift) => (
            <li key={lift.category} dir="auto">
              {t("console.season_lift", {
                category: lift.category,
                units: lift.units,
                times: lift.times,
              })}
            </li>
          ))}
        </ul>
      ) : (
        season.usual.length > 0 && (
          <p className="pay-note" dir="auto">
            {t("console.season_usual", { categories: season.usual.join(", ") })}
          </p>
        )
      )}
    </li>
  );
}

function Note({ note }: { note: CoachNote }) {
  const { t, lang } = useLang();
  return (
    <li className="coach-note">
      <p className="pay-note">
        {t("console.coach_week", {
          day: new Date(`${note.week}T00:00:00`).toLocaleDateString(lang, {
            day: "numeric",
            month: "short",
          }),
        })}
      </p>
      <ul>
        {note.lines.map((line) => (
          <li key={line} dir="auto">
            {line}
          </li>
        ))}
      </ul>
      {note.advice.length > 0 && (
        <>
          <p className="pay-note">{t("console.coach_advice")}</p>
          <ul className="coach-advice">
            {note.advice.map((line) => (
              <li key={line} dir="auto">
                {line}
              </li>
            ))}
          </ul>
        </>
      )}
    </li>
  );
}

export function CoachPanel({ token }: { token: string }) {
  const { t } = useLang();
  const loadNotes = useCallback(() => api.coachNotes(token), [token]);
  const loadSeasons = useCallback(() => api.seasons(token), [token]);
  const notes = usePolled(loadNotes, POLL_MS);
  const seasons = usePolled(loadSeasons, POLL_MS);
  const [making, setMaking] = useState(false);
  async function now() {
    setMaking(true);
    await api.coachNow(token).catch(() => undefined);
    setMaking(false);
    notes.refresh();
  }
  if (!notes.data || !seasons.data) return null;
  return (
    <section className="pay-panel coach" aria-labelledby="coach-title">
      <h2 id="coach-title" className="section-title">
        {t("console.coach_title")}
      </h2>
      <p className="aside">{t("console.coach_lede")}</p>
      <div className="pay-columns">
        <div className="pay-block">
          {notes.data.length === 0 ? (
            <p className="aside">{t("console.coach_none")}</p>
          ) : (
            <ul className="pay-rows">
              {notes.data.map((note) => (
                <Note key={note.week} note={note} />
              ))}
            </ul>
          )}
          <button
            type="button"
            className="button button-ink button-small"
            disabled={making}
            onClick={() => void now()}
          >
            {t("console.coach_now")}
          </button>
        </div>
        <div className="pay-block">
          <h3 className="pay-block-title">{t("console.seasons_title")}</h3>
          {seasons.data.length === 0 ? (
            <p className="aside">{t("console.seasons_none")}</p>
          ) : (
            <ul className="pay-rows">
              {seasons.data.map((season) => (
                <Season key={`${season.key}-${season.starts}`} season={season} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
