// The shop's Meta catalog (feature 6): how many products are in the WhatsApp catalog and the
// Instagram and Facebook shop, when they were last sent, what Meta said, and Send now.
import { useCallback, useState } from "react";
import { api } from "../api";
import { useLang } from "../lang";
import { usePolled } from "../usePolled";

const POLL_MS = 20000;

export function CatalogSyncPanel({ token }: { token: string }) {
  const { t, lang } = useLang();
  const load = useCallback(() => api.catalogSync(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [queued, setQueued] = useState(false);

  async function sendNow() {
    setQueued(true);
    await api.sendCatalog(token).catch(() => setQueued(false));
    refresh();
  }

  const when = data?.synced_at
    ? new Intl.DateTimeFormat(lang, { dateStyle: "medium", timeStyle: "short" }).format(
        new Date(data.synced_at),
      )
    : null;
  return (
    <section className="catalog-sync" aria-labelledby="catalog-sync-title" aria-busy={!data}>
      <h2 id="catalog-sync-title" className="section-title">
        {t("console.catalog_title")}
      </h2>
      <p className="posts-lede">{t("console.catalog_lede")}</p>
      {data && !data.connected && (
        <p className="posts-empty">{t("console.catalog_not_connected")}</p>
      )}
      {data?.connected && (
        <div className="catalog-sync-card">
          <p className="catalog-sync-line">
            <strong>{t("console.catalog_items", { count: data.items })}</strong>
            <span>{when ? t("console.catalog_synced", { when }) : t("console.catalog_never")}</span>
            {data.status && data.status !== "never" && (
              <span className={`catalog-sync-status is-${data.status}`}>
                {t("console.catalog_status", { status: data.status })}
              </span>
            )}
          </p>
          <button
            type="button"
            className="action action-primary"
            disabled={queued}
            onClick={() => void sendNow()}
          >
            {t("console.catalog_send")}
          </button>
          {queued && <p className="pay-note">{t("console.catalog_queued")}</p>}
          {data.left_out.length > 0 && (
            <p className="pay-note" dir="auto">
              {t("console.catalog_no_photo", { names: data.left_out.join(", ") })}
            </p>
          )}
          {data.errors.length > 0 && (
            <details className="post-comments">
              <summary>{t("console.catalog_errors")}</summary>
              <ul>
                {data.errors.map((error, index) => (
                  <li key={index}>
                    {error.id && <span dir="ltr">{error.id}</span>}
                    <span>{error.message}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
