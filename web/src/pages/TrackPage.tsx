// A parcel's page (feature 101): the QR code on its shipping label and the "on its way"
// message open it. It says where the order stands and what to pay at the door, and nothing
// about the address or the phone number.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../api";
import type { Parcel } from "../api";
import { Page, Problem } from "../components/Shell";
import { isolate } from "../i18n";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { useServer } from "../server";

function useParcel(code: string) {
  const { state } = useServer();
  const [parcel, setParcel] = useState<Parcel | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    if (state !== "online") return;
    let current = true;
    api.track(code).then(
      (found) => current && setParcel(found),
      (error: unknown) => {
        if (current && error instanceof ApiError && error.status === 404) setMissing(true);
      },
    );
    return () => {
      current = false;
    };
  }, [code, state]);
  return { parcel, missing };
}

export function TrackPage() {
  const { code = "" } = useParams();
  const { t, lang } = useLang();
  const { parcel, missing } = useParcel(code);

  if (missing) {
    return (
      <Page>
        <Problem message={t("track.not_found")} />
      </Page>
    );
  }
  if (!parcel) {
    return (
      <Page>
        <div className="slip slip-skeleton" aria-busy="true" />
      </Page>
    );
  }
  const when = (iso: string) =>
    new Date(iso).toLocaleString(lang, { dateStyle: "medium", timeStyle: "short" });
  const done = ["delivered", "returned", "cancelled"].includes(parcel.status);
  return (
    <Page>
      <section className="plain-hero track-hero">
        <h1 className="title">{t("track.title")}</h1>
        <p className="lede">
          {t("track.lede", { code: isolate(parcel.order), shop: isolate(parcel.shop) })}
        </p>
      </section>
      <article className="track">
        {!done && (
          <p className={parcel.collect ? "track-collect" : "track-paid"}>
            {parcel.collect
              ? t("track.collect", { amount: formatMoney(parcel.collect, parcel.country, lang) })
              : t("track.paid")}
          </p>
        )}
        <ol className="track-steps">
          {parcel.steps.map((step) => (
            <li key={step.step} className={`track-step step-${step.step}`}>
              <span className="track-step-name">{t(`track.step.${step.step}` as Key)}</span>
              <time dateTime={step.at}>{when(step.at)}</time>
            </li>
          ))}
        </ol>
        {(parcel.courier || parcel.tracking_number) && (
          <p className="track-courier" dir="auto">
            {t("track.courier", {
              courier: isolate(parcel.courier ?? "—"),
              tracking: isolate(parcel.tracking_number ?? "—"),
            })}
          </p>
        )}
        <Link to={`/s/${parcel.shop_slug}/chat`} className="action">
          {t("track.chat")}
        </Link>
      </article>
    </Page>
  );
}
