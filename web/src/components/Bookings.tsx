// Appointments (feature 18): the times customers took by tapping one in their chat, marked
// done, missed or cancelled (a cancelled customer is told in their chat), and the hours the
// shop takes bookings with how many people it serves at once. Code finds the free times; the
// owner only sets the hours.
import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, api } from "../api";
import type { Appointment, BookingHours, OwnerMe } from "../api";
import { DAYS, validHours, validSpan } from "../hours";
import { languageOf, localeOf } from "../i18n";
import type { Key, Lang } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 30_000;
const MAX_SEATS = 20;

/** "Monday" in the reader's language: 1 January 2024 was a Monday. */
function dayName(index: number, lang: Lang): string {
  return new Date(Date.UTC(2024, 0, 1 + index)).toLocaleDateString(localeOf(languageOf(lang)), {
    weekday: "long",
    timeZone: "UTC",
  });
}

function AppointmentRow({
  me,
  token,
  item,
  onChange,
}: {
  me: OwnerMe;
  token: string;
  item: Appointment;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  const [failed, setFailed] = useState(false);
  const starts = new Date(item.starts_at);
  const begun = starts.getTime() <= Date.now();
  const when = starts.toLocaleString(lang, {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
  function mark(to: "done" | "no_show" | "cancelled") {
    setFailed(false);
    void api.markBooking(token, item.id, to).then(onChange, () => setFailed(true));
  }
  return (
    <li className={`booking-row is-${item.status}`}>
      <p className="waiting-head">
        <time dateTime={item.starts_at}>{when}</time>
        <strong dir="auto">{item.service}</strong>
        <span dir="auto">{item.customer || t("console.customer_label")}</span>
        <span className="pay-code">{item.code}</span>
        <span className="pay-note">{formatMoney(item.price, me.country, lang)}</span>
        <span className={`order-status status-${item.status}`}>
          {t(`console.booking_status.${item.status}` as Key)}
        </span>
      </p>
      {item.status === "booked" && (
        <div className="waiting-actions">
          {begun ? (
            <>
              <button type="button" className="action" onClick={() => mark("done")}>
                {t("console.booking_done")}
              </button>
              <button type="button" className="action" onClick={() => mark("no_show")}>
                {t("console.booking_no_show")}
              </button>
            </>
          ) : (
            <button type="button" className="action" onClick={() => mark("cancelled")}>
              {t("console.booking_cancel")}
            </button>
          )}
        </div>
      )}
      {failed && (
        <p className="order-failed" role="alert">
          {t("console.move_failed")}
        </p>
      )}
    </li>
  );
}

function HoursForm({
  token,
  hours,
  onSaved,
}: {
  token: string;
  hours: BookingHours;
  onSaved: () => void;
}) {
  const { t, lang } = useLang();
  const [days, setDays] = useState<Record<string, string>>(hours.days);
  const [seats, setSeats] = useState(String(hours.seats));
  const [state, setState] = useState<"idle" | "saving" | "saved" | "bad_hours" | "failed">("idle");

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!validHours(days)) {
      setState("bad_hours");
      return;
    }
    setState("saving");
    try {
      const count = Math.min(MAX_SEATS, Math.max(1, Math.round(Number(seats) || 1)));
      await api.saveBookingHours(token, { days, seats: count });
      setState("saved");
      onSaved();
    } catch (error) {
      setState(error instanceof ApiError && error.code === "bad_hours" ? "bad_hours" : "failed");
    }
  }

  return (
    <form className="books-form booking-hours" onSubmit={(event) => void save(event)}>
      <h3 className="pay-block-title">{t("console.booking_hours")}</h3>
      <p className="aside">{t("console.booking_hours_note")}</p>
      {DAYS.map((day, index) => (
        <label key={day}>
          <span>{dayName(index, lang)}</span>
          <input
            value={days[day] ?? ""}
            placeholder={t("console.booking_closed")}
            inputMode="numeric"
            maxLength={11}
            dir="ltr"
            aria-invalid={validSpan(days[day] ?? "") ? undefined : true}
            onChange={(e) => setDays({ ...days, [day]: e.target.value })}
          />
        </label>
      ))}
      <label>
        <span>{t("console.booking_seats")}</span>
        <input
          value={seats}
          inputMode="numeric"
          maxLength={2}
          dir="ltr"
          onChange={(e) => setSeats(e.target.value.replace(/\D/g, ""))}
        />
      </label>
      <div className="wording-actions">
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={state === "saving"}
        >
          {t("console.booking_hours_save")}
        </button>
        {state === "saved" && <span role="status">{t("console.saved")}</span>}
        {state === "bad_hours" && <span role="alert">{t("console.err.bad_hours")}</span>}
        {state === "failed" && <span role="alert">{t("console.offers_failed")}</span>}
      </div>
    </form>
  );
}

export function BookingsPanel({
  me,
  token,
  canSettings,
}: {
  me: OwnerMe;
  token: string;
  canSettings: boolean;
}) {
  const { t } = useLang();
  const load = useCallback(() => api.bookings(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  if (!data || (me.template !== "services" && data.coming.length === 0)) return null;
  return (
    <section className="pay-panel bookings" aria-labelledby="bookings-title">
      <h2 id="bookings-title" className="section-title">
        {t("console.bookings_title")}
        {data.coming.length > 0 && <span className="console-count">{data.coming.length}</span>}
      </h2>
      <p className="aside">{t("console.bookings_lede")}</p>
      <div className="pay-columns">
        <div className="pay-block">
          {data.coming.length === 0 ? (
            <p className="aside">{t("console.bookings_none")}</p>
          ) : (
            <ul className="pay-rows">
              {data.coming.map((item) => (
                <AppointmentRow
                  key={item.id}
                  me={me}
                  token={token}
                  item={item}
                  onChange={refresh}
                />
              ))}
            </ul>
          )}
        </div>
        {canSettings && (
          <div className="pay-block">
            <HoursForm token={token} hours={data.hours} onSaved={refresh} />
          </div>
        )}
      </div>
    </section>
  );
}
