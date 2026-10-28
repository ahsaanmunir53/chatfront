// The buyer's review page (feature 46): the "delivered" message links here, signed for that
// one order, so only the buyer it reached can rate it, once.
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { ApiError, api } from "../api";
import type { ReviewForm } from "../api";
import { Stars } from "../components/Reviews";
import { Page, Problem } from "../components/Shell";
import { useLang } from "../lang";
import { useServer } from "../server";

export function ReviewPage() {
  const { slug = "", code = "" } = useParams();
  const [search] = useSearchParams();
  const signature = search.get("s") ?? "";
  const { t, lang } = useLang();
  const { state } = useServer();
  const [form, setForm] = useState<ReviewForm | null>(null);
  const [missing, setMissing] = useState(false);
  const [rating, setRating] = useState(0);
  const [text, setText] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (state !== "online") return;
    let current = true;
    api.reviewForm(slug, code, signature).then(
      (found) => current && setForm(found),
      (error: unknown) => {
        if (current && error instanceof ApiError && error.status === 404) setMissing(true);
      },
    );
    return () => {
      current = false;
    };
  }, [slug, code, signature, state]);

  async function send(event: FormEvent) {
    event.preventDefault();
    try {
      setForm(await api.sendReview(slug, code, signature, { rating, text: text.trim(), lang }));
    } catch {
      setFailed(true);
    }
  }

  if (missing) {
    return (
      <Page>
        <Problem message={t("reviews.not_found")} />
      </Page>
    );
  }
  if (!form) {
    return (
      <Page>
        <div className="slip slip-skeleton" aria-busy="true" />
      </Page>
    );
  }
  return (
    <Page>
      <section className="plain-hero">
        <h1 className="title">{t("reviews.page_title", { shop: form.shop })}</h1>
        <p className="lede" dir="auto">
          {t("reviews.page_order", { code: form.code, items: form.items.join(", ") })}
        </p>
      </section>
      <section className="plain-section review-page">
        {form.review ? (
          <div role="status">
            <p>{t("reviews.thanks")}</p>
            <Stars rating={form.review.rating} />
            {form.review.text && <p dir="auto">{form.review.text}</p>}
          </div>
        ) : !form.delivered ? (
          <p>{t("reviews.not_yet")}</p>
        ) : (
          <form className="books-form" onSubmit={(event) => void send(event)}>
            <fieldset className="star-pick">
              <legend>{t("reviews.your_stars")}</legend>
              {[1, 2, 3, 4, 5].map((n) => (
                <label key={n} className={n <= rating ? "is-on" : undefined}>
                  <input
                    type="radio"
                    name="rating"
                    value={n}
                    checked={rating === n}
                    onChange={() => setRating(n)}
                  />
                  <span aria-hidden="true">★</span>
                  <span className="sr-only">{t("reviews.stars", { count: n })}</span>
                </label>
              ))}
            </fieldset>
            <label>
              {t("reviews.your_words")}
              <textarea
                value={text}
                rows={4}
                maxLength={500}
                dir="auto"
                onChange={(e) => setText(e.target.value)}
              />
            </label>
            <p className="pay-note">{t("reviews.public_note")}</p>
            <button type="submit" className="button button-ink" disabled={rating === 0}>
              {t("reviews.send")}
            </button>
            {failed && <p role="alert">{t("reviews.failed")}</p>}
          </form>
        )}
      </section>
    </Page>
  );
}
