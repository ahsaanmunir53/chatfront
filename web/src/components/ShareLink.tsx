// A link to pass on: the phone's own share sheet where there is one, else a copy button.
import { useCopy } from "../clipboard";
import { useLang } from "../lang";

export function ShareLink({ url, title }: { url: string; title: string }) {
  const { t } = useLang();
  const { copied, copy } = useCopy();
  async function share() {
    if (navigator.share) {
      await navigator.share({ title, url }).catch(() => undefined);
      return;
    }
    await copy(url);
  }
  return (
    <div className="share-link">
      <input readOnly value={url} dir="ltr" aria-label={title} onFocus={(e) => e.target.select()} />
      <button type="button" className="button button-ink button-small" onClick={() => void share()}>
        {t("group.share")}
      </button>
      {copied && <span role="status">{t("group.copied")}</span>}
    </div>
  );
}
