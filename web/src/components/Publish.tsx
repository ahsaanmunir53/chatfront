// Posts code drafts (features 7 and 82): the morning post of new stock and one product put up
// everywhere. The owner looks at the pictures and the captions, ticks where it goes and says
// post; nothing goes out before. A WhatsApp Status has no API, so its picture is here to save
// and share by hand.
import { useCallback, useState } from "react";
import { ApiError, api } from "../api";
import type { PostTarget, Publication, Publications } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 15_000;
const TARGETS: PostTarget[] = ["instagram", "facebook", "whatsapp_catalog", "whatsapp_status"];

function connected(data: Publications, target: PostTarget): boolean {
  return target === "whatsapp_status" || data.connected[target];
}

function Draft({
  token,
  data,
  item,
  onChange,
}: {
  token: string;
  data: Publications;
  item: Publication;
  onChange: () => void;
}) {
  const { t } = useLang();
  const [chosen, setChosen] = useState<PostTarget[]>(() =>
    TARGETS.filter((target) => connected(data, target)),
  );
  const [failed, setFailed] = useState(false);
  async function send() {
    setFailed(false);
    try {
      await api.sendPublication(token, item.id, chosen);
      onChange();
    } catch {
      setFailed(true);
    }
  }
  return (
    <>
      <fieldset className="post-targets">
        <legend>{t("console.post_where")}</legend>
        {TARGETS.map((target) => (
          <label key={target} className="offer-check">
            <input
              type="checkbox"
              checked={chosen.includes(target)}
              disabled={!connected(data, target)}
              onChange={() =>
                setChosen(
                  chosen.includes(target)
                    ? chosen.filter((x) => x !== target)
                    : [...chosen, target],
                )
              }
            />
            <span>
              {t(`console.post_target.${target}` as Key)}
              {!connected(data, target) && ` (${t("console.post_not_connected")})`}
            </span>
          </label>
        ))}
      </fieldset>
      <div className="wording-actions">
        <button
          type="button"
          className="button button-ink button-small"
          disabled={chosen.length === 0}
          onClick={() => void send()}
        >
          {t("console.post_send")}
        </button>
        <button
          type="button"
          className="action"
          onClick={() => void api.skipPublication(token, item.id).then(onChange, onChange)}
        >
          {t("console.post_skip")}
        </button>
        {failed && <span role="alert">{t("console.offers_failed")}</span>}
      </div>
    </>
  );
}

function PublicationCard({
  token,
  data,
  item,
  onChange,
}: {
  token: string;
  data: Publications;
  item: Publication;
  onChange: () => void;
}) {
  const { t, lang } = useLang();
  const made = new Date(item.created_at).toLocaleString(lang, {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
  return (
    <li className="post-card">
      <p>
        <strong>{t(`console.post_kind.${item.kind}` as Key)}</strong>{" "}
        <span className="pay-note">{made}</span>{" "}
        <span className="pay-note">{t(`console.post_status.${item.status}` as Key)}</span>
      </p>
      <div className="post-images">
        {item.images.map((image) => (
          <figure key={image.post_url}>
            <img src={image.post_url} alt={image.product} loading="lazy" />
            <figcaption dir="auto">
              {image.product}{" "}
              <a href={image.status_url} download>
                {t("console.post_status_picture")}
              </a>
            </figcaption>
          </figure>
        ))}
      </div>
      {Object.entries(item.captions).map(([tag, text]) => (
        <p key={tag} className="post-caption" lang={tag} dir="auto">
          {text}
        </p>
      ))}
      <p className="pay-note">
        {item.by_model ? t("console.post_words_model") : t("console.post_words_code")}
      </p>
      {item.status === "draft" ? (
        <Draft token={token} data={data} item={item} onChange={onChange} />
      ) : (
        <ul className="post-results">
          {Object.entries(item.targets).map(([target, result]) => (
            <li key={target}>
              {t(`console.post_target.${target}` as Key)}:{" "}
              {t(`console.post_outcome.${result?.status ?? "waiting"}` as Key)}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function PublicationsPanel({ token }: { token: string }) {
  const { t } = useLang();
  const load = useCallback(() => api.publications(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [making, setMaking] = useState<"idle" | "making" | string>("idle");
  if (!data) return null;
  async function today() {
    setMaking("making");
    try {
      await api.postToday(token);
      setMaking("idle");
      refresh();
    } catch (error) {
      setMaking(error instanceof ApiError ? error.code : "failed");
    }
  }
  return (
    <section className="pay-panel publications" aria-labelledby="publications-title">
      <h2 id="publications-title" className="section-title">
        {t("console.posts_new_title")}
      </h2>
      <p className="aside">{t("console.posts_new_lede")}</p>
      <div className="wording-actions">
        <label className="offer-check">
          <input
            type="checkbox"
            checked={data.daily}
            onChange={(e) => void api.setDailyPost(token, e.target.checked).then(refresh, refresh)}
          />
          <span>{t("console.post_daily")}</span>
        </label>
        <button
          type="button"
          className="button button-ink button-small"
          disabled={making === "making"}
          onClick={() => void today()}
        >
          {t("console.post_today")}
        </button>
        {making === "nothing_new" && <span role="status">{t("console.post_nothing_new")}</span>}
        {making === "failed" && <span role="alert">{t("console.offers_failed")}</span>}
      </div>
      {data.items.length === 0 ? (
        <p className="aside">{t("console.posts_none_yet")}</p>
      ) : (
        <ul className="pay-rows">
          {data.items.map((item) => (
            <PublicationCard
              key={item.id}
              token={token}
              data={data}
              item={item}
              onChange={refresh}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
