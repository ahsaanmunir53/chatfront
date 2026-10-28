// A product's pictures in the console (features 3 and 82): its photo cleaned by code, to use
// if the owner likes it better; a card for a Status or a post to save; and the product put up
// on every channel, as a draft under Posts.
import { useState } from "react";
import { ApiError, api } from "../api";
import type { Picture } from "../api";
import { useLang } from "../lang";

type Shape = "square" | "post" | "status";
const SHAPES: Shape[] = ["square", "post", "status"];

export function ProductPictures({ token, code }: { token: string; code: string }) {
  const { t } = useLang();
  const [cleaned, setCleaned] = useState<Picture | null>(null);
  const [card, setCard] = useState<Picture | null>(null);
  const [note, setNote] = useState<"" | "used" | "posted" | "no_photo" | "failed">("");

  async function step(run: () => Promise<void>) {
    setNote("");
    try {
      await run();
    } catch (error) {
      setNote(error instanceof ApiError && error.code === "no_photo" ? "no_photo" : "failed");
    }
  }

  return (
    <div className="product-pictures">
      <div className="wording-actions">
        <button
          type="button"
          className="action"
          onClick={() => void step(async () => setCleaned(await api.cleanPhoto(token, code)))}
        >
          {t("console.photo_clean")}
        </button>
        {SHAPES.map((shape) => (
          <button
            key={shape}
            type="button"
            className="action"
            onClick={() =>
              void step(async () => setCard(await api.productCard(token, code, shape)))
            }
          >
            {t(`console.photo_card.${shape}`)}
          </button>
        ))}
        <button
          type="button"
          className="action"
          onClick={() =>
            void step(async () => {
              await api.postProduct(token, code);
              setNote("posted");
            })
          }
        >
          {t("console.post_everywhere")}
        </button>
      </div>
      {cleaned && (
        <figure className="picture-preview">
          <img src={cleaned.url} alt={t("console.photo_cleaned")} />
          <figcaption>
            <button
              type="button"
              className="button button-ink button-small"
              onClick={() =>
                void step(async () => {
                  await api.usePhoto(token, code, cleaned.media_id);
                  setNote("used");
                })
              }
            >
              {t("console.photo_use")}
            </button>
          </figcaption>
        </figure>
      )}
      {card && (
        <figure className="picture-preview">
          <img src={card.url} alt={t("console.photo_card_alt")} />
          <figcaption>
            <a href={card.url} download>
              {t("console.photo_save")}
            </a>
          </figcaption>
        </figure>
      )}
      {note && (
        <p role={note === "failed" || note === "no_photo" ? "alert" : "status"}>
          {t(`console.photo_note.${note}`)}
        </p>
      )}
    </div>
  );
}
