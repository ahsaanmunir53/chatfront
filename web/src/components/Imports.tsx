// Bringing in what the shop already has (features 85, 84 and 2): a sheet, the Meta catalog,
// Instagram posts or a video of a rack. What is found waits here as drafts; the owner checks
// the name and price and adds each one, or adds every draft whose price they wrote themselves.
import { useCallback, useState } from "react";
import type { ChangeEvent } from "react";
import { ApiError, api } from "../api";
import type { ImportDraft, ImportRun, ImportSource, OwnerMe } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney, packOf, parseAmount } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 8000;
const SOURCE: Record<ImportSource, Key> = {
  sheet: "imports.source_sheet",
  meta_catalog: "imports.source_meta_catalog",
  instagram: "imports.source_instagram",
  video: "imports.source_video",
};
// Where a price came from, when the owner should look at it before adding.
const PRICE_FROM: Record<string, Key> = {
  image: "imports.price_image",
  said: "imports.price_said",
  tag: "imports.price_tag",
};
const ERRORS: Record<string, Key> = {
  no_columns: "imports.error_columns",
  model_unavailable: "imports.error_model",
  mock_fixture_missing: "imports.error_model",
  instagram_not_connected: "imports.error_instagram",
  catalog_not_connected: "imports.error_catalog",
  import_running: "imports.error_running",
  not_a_video: "imports.error_video",
  media_too_large: "imports.error_size",
  sheet_too_big: "imports.error_size",
  draft_incomplete: "imports.error_incomplete",
};

function errorKey(code: string | null | undefined): Key {
  return (code && ERRORS[code]) || "imports.error";
}

function RunLine({
  run,
  onAddReady,
  onDropRest,
}: {
  run: ImportRun;
  onAddReady: (run: ImportRun) => void;
  onDropRest: (run: ImportRun) => void;
}) {
  const { t } = useLang();
  return (
    <li className={`import-run is-${run.status}`}>
      <span className="import-run-source">{t(SOURCE[run.source])}</span>
      <span className="import-run-label" dir="auto">
        {run.label}
      </span>
      <span className="import-run-status" role={run.status === "failed" ? "alert" : undefined}>
        {run.status === "reading"
          ? t("imports.reading")
          : run.status === "failed"
            ? t(errorKey(run.error))
            : t("imports.found", { count: run.found, waiting: run.waiting })}
      </span>
      {run.status === "ready" && run.waiting > 0 && (
        <span className="import-run-actions">
          <button type="button" className="action" onClick={() => onAddReady(run)}>
            {t("imports.add_ready")}
          </button>
          <button type="button" className="action" onClick={() => onDropRest(run)}>
            {t("imports.drop_rest")}
          </button>
        </span>
      )}
    </li>
  );
}

function DraftCard({
  me,
  token,
  draft,
  onDone,
}: {
  me: OwnerMe;
  token: string;
  draft: ImportDraft;
  onDone: () => void;
}) {
  const { t, lang } = useLang();
  const digits = packOf(me.country).minorDigits;
  const [name, setName] = useState(draft.name);
  const [price, setPrice] = useState(
    draft.price === null ? "" : String(draft.price / 10 ** digits),
  );
  const [problem, setProblem] = useState<Key | null>(null);
  const [busy, setBusy] = useState(false);
  const check = draft.price_from ? PRICE_FROM[draft.price_from] : undefined;

  async function add() {
    const major = parseAmount(price);
    if (!name.trim() || major === null) {
      setProblem("imports.error_incomplete");
      return;
    }
    setBusy(true);
    try {
      await api.addDraft(token, draft.id, { name: name.trim(), price: major });
      onDone();
    } catch (error) {
      setProblem(errorKey(error instanceof ApiError ? error.code : null));
      setBusy(false);
    }
  }

  async function drop() {
    setBusy(true);
    await api.dropDraft(token, draft.id).catch(() => setBusy(false));
    onDone();
  }

  const options = draft.variants.map((v) => Object.values(v.options).join(" ")).filter(Boolean);
  return (
    <li className="import-draft">
      {draft.photo ? (
        <img className="import-photo" src={draft.photo} alt="" loading="lazy" />
      ) : (
        <span className="import-photo is-empty" aria-hidden="true" />
      )}
      <div className="import-fields books-form">
        <label>
          {t("imports.name")}
          <input
            value={name}
            dir="auto"
            maxLength={160}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          {t("imports.price", { currency: me.currency })}
          <input
            value={price}
            inputMode="decimal"
            placeholder={t("imports.price_missing")}
            onChange={(e) => setPrice(e.target.value)}
          />
        </label>
        {check && <p className="import-check">{t(check)}</p>}
        {options.length > 0 && (
          <p className="pay-note" dir="auto">
            {t("imports.options", { options: options.join(", ") })}
          </p>
        )}
        {draft.in_shop && (
          <p className="import-check" dir="auto">
            {t("imports.in_shop", { name: draft.in_shop })}
          </p>
        )}
        {draft.link && (
          <a className="pay-note" href={draft.link} target="_blank" rel="noreferrer">
            {t("imports.open_post")}
          </a>
        )}
        {draft.price !== null && !check && (
          <p className="pay-note">{formatMoney(draft.price, me.country, lang)}</p>
        )}
        <div className="import-actions">
          <button
            type="button"
            className="button button-ink button-small"
            disabled={busy}
            onClick={() => void add()}
          >
            {t("imports.add")}
          </button>
          <button type="button" className="action" disabled={busy} onClick={() => void drop()}>
            {t("imports.drop")}
          </button>
          {problem && <span role="alert">{t(problem)}</span>}
        </div>
      </div>
    </li>
  );
}

function FilePick({
  label,
  accept,
  onPick,
}: {
  label: string;
  accept: string;
  onPick: (file: File) => void;
}) {
  return (
    <label className="action import-pick">
      {label}
      <input
        className="sr-only"
        type="file"
        accept={accept}
        onChange={(event: ChangeEvent<HTMLInputElement>) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) onPick(file);
        }}
      />
    </label>
  );
}

export function ImportsPanel({
  me,
  token,
  onAdded,
}: {
  me: OwnerMe;
  token: string;
  onAdded: () => void;
}) {
  const { t } = useLang();
  const load = useCallback(() => api.imports(token), [token]);
  const { data, refresh } = usePolled(load, POLL_MS);
  const [note, setNote] = useState<{ key: Key; values?: Record<string, number> } | null>(null);

  const done = useCallback(() => {
    refresh();
    onAdded();
  }, [refresh, onAdded]);

  async function start(step: () => Promise<unknown>, startedKey: Key) {
    setNote(null);
    try {
      await step();
      setNote({ key: startedKey });
    } catch (error) {
      setNote({ key: errorKey(error instanceof ApiError ? error.code : null) });
    }
    refresh();
  }

  async function addReady(run: ImportRun) {
    const { added } = await api.addReady(token, run.id);
    setNote({ key: "imports.added", values: { count: added } });
    done();
  }

  async function dropRest(run: ImportRun) {
    await api.dropRest(token, run.id);
    refresh();
  }

  return (
    <section className="pay-panel imports" aria-labelledby="imports-title" aria-busy={!data}>
      <h2 id="imports-title" className="section-title">
        {t("imports.title")}
      </h2>
      <p className="aside">{t("imports.lede")}</p>
      <div className="import-sources">
        <FilePick
          label={t("imports.sheet")}
          accept=".xlsx,.csv,text/csv"
          onPick={(file) => void start(() => api.importSheet(token, file), "imports.sheet_read")}
        />
        <FilePick
          label={t("imports.video")}
          accept="video/mp4,video/quicktime"
          onPick={(file) => void start(() => api.importVideo(token, file), "imports.started")}
        />
        <button
          type="button"
          className="action"
          disabled={!data?.instagram}
          onClick={() => void start(() => api.importFrom(token, "instagram"), "imports.started")}
        >
          {t("imports.instagram")}
        </button>
        <button
          type="button"
          className="action"
          disabled={!data?.meta_catalog}
          onClick={() => void start(() => api.importFrom(token, "meta-catalog"), "imports.started")}
        >
          {t("imports.meta_catalog")}
        </button>
      </div>
      <p className="pay-note">{t("imports.hint")}</p>
      {note && (
        <p className="pay-note" role="status">
          {t(note.key, note.values)}
        </p>
      )}
      {data && data.runs.length > 0 && (
        <ul className="import-runs" aria-label={t("imports.runs")}>
          {data.runs.map((run) => (
            <RunLine
              key={run.id}
              run={run}
              onAddReady={(r) => void addReady(r)}
              onDropRest={(r) => void dropRest(r)}
            />
          ))}
        </ul>
      )}
      {data && data.drafts.length > 0 && (
        <ul className="import-drafts" aria-label={t("imports.drafts")}>
          {data.drafts.map((draft) => (
            <DraftCard key={draft.id} me={me} token={token} draft={draft} onDone={done} />
          ))}
        </ul>
      )}
    </section>
  );
}
