// Reviews from verified buyers only (feature 46): only a buyer whose order was delivered can
// review it, once. The shop page shows them; the owner can reply once and cannot remove them.
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api } from "../api";
import type { ShopReview, ShopReviews } from "../api";
import { useLang } from "../lang";
import { useServer } from "../server";
import { usePolled } from "../usePolled";

const POLL_MS = 30_000;

export function Stars({ rating }: { rating: number }) {
  const { t } = useLang();
  return (
    <span className="stars" role="img" aria-label={t("reviews.stars", { count: rating })}>
      {"★".repeat(rating)}
      <span className="stars-off">{"★".repeat(5 - rating)}</span>
    </span>
  );
}

function ReviewItem({ review, children }: { review: ShopReview; children?: React.ReactNode }) {
  const { t, lang } = useLang();
  const when = new Intl.DateTimeFormat(lang, { dateStyle: "medium" }).format(
    new Date(review.created_at),
  );
  return (
    <li className="review">
      <div className="review-head">
        <Stars rating={review.rating} />
        <span className="review-who" dir="auto">
          {review.name ?? t("reviews.a_buyer")}
        </span>
        <span className="review-badge">{t("reviews.verified")}</span>
        <span className="pay-note">{when}</span>
      </div>
      {review.items.length > 0 && (
        <p className="pay-note" dir="auto">
          {t("reviews.bought", { items: review.items.join(", ") })}
        </p>
      )}
      {review.text && (
        <p className="review-text" dir="auto">
          {review.text}
        </p>
      )}
      {review.reply && (
        <p className="review-reply" dir="auto">
          <strong>{t("reviews.reply_label")}</strong> {review.reply}
        </p>
      )}
      {children}
    </li>
  );
}

export function Summary({ data }: { data: ShopReviews }) {
  const { t } = useLang();
  if (data.average === null) return null;
  return (
    <p className="review-summary">
      <Stars rating={Math.round(data.average)} />
      {t("reviews.summary", { average: data.average.toFixed(1), count: data.count })}
    </p>
  );
}

/** The latest reviews on the shop page; nothing until the first one. */
export function ShopReviewsSection({ slug }: { slug: string }) {
  const { t } = useLang();
  const { state } = useServer();
  const [data, setData] = useState<ShopReviews | null>(null);
  useEffect(() => {
    if (state !== "online") return;
    let current = true;
    api.shopReviews(slug).then(
      (found) => current && setData(found),
      () => {
        // The shop page works without its reviews.
      },
    );
    return () => {
      current = false;
    };
  }, [slug, state]);
  if (!data || data.count === 0) return null;
  return (
    <section className="reviews" aria-labelledby="reviews-title">
      <h2 id="reviews-title" className="section-title">
        {t("reviews.title")}
      </h2>
      <Summary data={data} />
      <ul className="review-list">
        {data.reviews.map((review) => (
          <ReviewItem key={review.id} review={review} />
        ))}
      </ul>
    </section>
  );
}

function ReplyForm({
  token,
  review,
  onDone,
}: {
  token: string;
  review: ShopReview;
  onDone: () => void;
}) {
  const { t } = useLang();
  const [text, setText] = useState("");
  const [failed, setFailed] = useState(false);
  async function send(event: FormEvent) {
    event.preventDefault();
    try {
      await api.replyToReview(token, review.id, text.trim());
      onDone();
    } catch {
      setFailed(true);
    }
  }
  return (
    <form className="books-form review-reply-form" onSubmit={(event) => void send(event)}>
      <label>
        {t("reviews.reply")}
        <input value={text} maxLength={500} dir="auto" onChange={(e) => setText(e.target.value)} />
      </label>
      <button
        type="submit"
        className="button button-ink button-small"
        disabled={text.trim().length < 2}
      >
        {t("reviews.send_reply")}
      </button>
      {failed && <p role="alert">{t("reviews.reply_failed")}</p>}
    </form>
  );
}

/** Reviews in the console, with the owner's one reply each. */
export function ReviewsPanel({ token, canReply }: { token: string; canReply: boolean }) {
  const { t } = useLang();
  const load = useCallback(() => api.ownerReviews(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  if (!data) return null;
  return (
    <section className="pay-panel reviews" aria-labelledby="owner-reviews-title">
      <h2 id="owner-reviews-title" className="section-title">
        {t("reviews.title")}
        <span className="console-count">{data.count}</span>
      </h2>
      <p className="aside">{t("reviews.owner_lede")}</p>
      <Summary data={data} />
      {data.count === 0 && <p className="aside">{t("reviews.none")}</p>}
      <ul className="review-list">
        {data.reviews.map((review) => (
          <ReviewItem key={review.id} review={review}>
            {canReply && !review.reply && (
              <ReplyForm token={token} review={review} onDone={refresh} />
            )}
          </ReviewItem>
        ))}
      </ul>
    </section>
  );
}
