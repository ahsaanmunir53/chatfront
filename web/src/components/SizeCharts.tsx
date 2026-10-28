// Size charts in the console (feature 5): each size's body measurements per kind of product.
// The shop page shows the chart with every product it covers; in chat, code uses it to find
// the customer's size from their measurements.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { Measure, SizeChart, SizeCharts, SizeRow } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { MEASURES, MEASURE_LABEL, measuresOf } from "./SizeChart";

type Saving = "idle" | "saving" | "saved" | "failed";
// A chart as the form holds it: ranges as typed ("34-36").
type Draft = {
  name: string;
  unit: "in" | "cm";
  categories: string[];
  measures: Measure[];
  rows: { size: string; cells: Partial<Record<Measure, string>> }[];
};

function toDraft(chart: SizeChart): Draft {
  return {
    name: chart.name,
    unit: chart.unit,
    categories: chart.categories ?? [],
    measures: measuresOf(chart),
    rows: chart.rows.map((row) => ({
      size: row.size,
      cells: Object.fromEntries(
        Object.entries(row.ranges).map(([measure, [low, high]]) => [
          measure,
          low === high ? `${low}` : `${low}-${high}`,
        ]),
      ),
    })),
  };
}

/** "34-36" or "34" as a range; null when it does not read. */
export function readRange(typed: string): [number, number] | null {
  const parts = typed
    .replace(/[–—]/g, "-")
    .split("-")
    .map((p) => p.trim());
  const numbers = parts.map(Number);
  if (parts.length > 2 || parts.some((p) => !p) || numbers.some((n) => !(n > 0))) return null;
  const [low, high = low] = numbers as [number, number?];
  return low <= high ? [low, high] : null;
}

function toChart(draft: Draft): SizeChart | null {
  const rows: SizeRow[] = [];
  for (const row of draft.rows) {
    const ranges: SizeRow["ranges"] = {};
    for (const measure of draft.measures) {
      const typed = row.cells[measure]?.trim();
      if (!typed) continue;
      const range = readRange(typed);
      if (!range) return null;
      ranges[measure] = range;
    }
    if (row.size.trim() && Object.keys(ranges).length > 0)
      rows.push({ size: row.size.trim(), ranges });
  }
  if (!draft.name.trim() || rows.length === 0) return null;
  return { name: draft.name.trim(), unit: draft.unit, categories: draft.categories, rows };
}

const EMPTY: Draft = {
  name: "",
  unit: "in",
  categories: [],
  measures: ["chest", "waist", "hips"],
  rows: [{ size: "", cells: {} }],
};

function ChartForm({
  draft,
  categories,
  onChange,
  onRemove,
}: {
  draft: Draft;
  categories: string[];
  onChange: (next: Draft) => void;
  onRemove: () => void;
}) {
  const { t } = useLang();
  const set = (patch: Partial<Draft>) => onChange({ ...draft, ...patch });
  const toggle = <T,>(list: T[], value: T) =>
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  const setCell = (index: number, measure: Measure, value: string) =>
    set({
      rows: draft.rows.map((row, i) =>
        i === index ? { ...row, cells: { ...row.cells, [measure]: value } } : row,
      ),
    });
  return (
    <fieldset className="size-chart-form books-form">
      <legend dir="auto">{draft.name || t("sizes.new_chart")}</legend>
      <label>
        {t("sizes.name")}
        <input
          value={draft.name}
          maxLength={40}
          dir="auto"
          onChange={(e) => set({ name: e.target.value })}
        />
      </label>
      <label>
        {t("sizes.unit")}
        <select value={draft.unit} onChange={(e) => set({ unit: e.target.value as Draft["unit"] })}>
          <option value="in">{t("size.unit_in")}</option>
          <option value="cm">{t("size.unit_cm")}</option>
        </select>
      </label>
      {categories.length > 0 && (
        <fieldset className="offer-products">
          <legend>{t("sizes.categories")}</legend>
          {categories.map((category) => (
            <label key={category} className="offer-check">
              <input
                type="checkbox"
                checked={draft.categories.includes(category)}
                onChange={() => set({ categories: toggle(draft.categories, category) })}
              />
              <span dir="auto">{category}</span>
            </label>
          ))}
        </fieldset>
      )}
      <fieldset className="offer-products size-measures">
        <legend>{t("sizes.measures")}</legend>
        {MEASURES.map((measure) => (
          <label key={measure} className="offer-check">
            <input
              type="checkbox"
              checked={draft.measures.includes(measure)}
              onChange={() =>
                set({
                  measures: MEASURES.filter((m) => toggle(draft.measures, measure).includes(m)),
                })
              }
            />
            {t(MEASURE_LABEL[measure])}
          </label>
        ))}
      </fieldset>
      <div className="size-chart-scroll">
        <table className="size-chart-edit">
          <thead>
            <tr>
              <th scope="col">{t("size.size")}</th>
              {draft.measures.map((measure) => (
                <th key={measure} scope="col">
                  {t(MEASURE_LABEL[measure])}
                </th>
              ))}
              <th scope="col">
                <span className="sr-only">{t("sizes.remove_row")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {draft.rows.map((row, index) => (
              <tr key={index}>
                <td>
                  <input
                    aria-label={t("size.size")}
                    value={row.size}
                    maxLength={12}
                    onChange={(e) =>
                      set({
                        rows: draft.rows.map((r, i) =>
                          i === index ? { ...r, size: e.target.value } : r,
                        ),
                      })
                    }
                  />
                </td>
                {draft.measures.map((measure) => (
                  <td key={measure}>
                    <input
                      aria-label={`${row.size} ${t(MEASURE_LABEL[measure])}`}
                      inputMode="decimal"
                      placeholder="34-36"
                      value={row.cells[measure] ?? ""}
                      onChange={(e) => setCell(index, measure, e.target.value)}
                    />
                  </td>
                ))}
                <td>
                  <button
                    type="button"
                    className="action"
                    onClick={() => set({ rows: draft.rows.filter((_, i) => i !== index) })}
                  >
                    {t("sizes.remove_row")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="import-actions">
        <button
          type="button"
          className="action"
          onClick={() => set({ rows: [...draft.rows, { size: "", cells: {} }] })}
        >
          {t("sizes.add_row")}
        </button>
        <button type="button" className="action" onClick={onRemove}>
          {t("sizes.remove_chart")}
        </button>
      </div>
    </fieldset>
  );
}

export function SizeChartsPanel({ token }: { token: string }) {
  const { t } = useLang();
  const [data, setData] = useState<SizeCharts | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [state, setState] = useState<Saving>("idle");

  const show = useCallback((found: SizeCharts) => {
    setData(found);
    setDrafts(found.charts.map(toDraft));
  }, []);

  useEffect(() => {
    api.sizeCharts(token).then(show, () => {
      // The panel waits for the next visit; nothing else depends on it.
    });
  }, [token, show]);

  async function save() {
    const charts = drafts.map(toChart);
    if (charts.some((chart) => chart === null)) {
      setState("failed");
      return;
    }
    setState("saving");
    try {
      show(await api.saveSizeCharts(token, charts as SizeChart[]));
      setState("saved");
    } catch {
      setState("failed");
    }
  }

  const status: Partial<Record<Saving, Key>> = { saved: "console.saved", failed: "sizes.failed" };
  return (
    <section
      className="pay-panel size-charts"
      aria-labelledby="size-charts-title"
      aria-busy={!data}
    >
      <h2 id="size-charts-title" className="section-title">
        {t("sizes.title")}
      </h2>
      <p className="aside">{t("sizes.lede")}</p>
      {data && drafts.length === 0 && <p className="aside">{t("sizes.none")}</p>}
      {drafts.map((draft, index) => (
        <ChartForm
          key={index}
          draft={draft}
          categories={data?.categories ?? []}
          onChange={(next) => setDrafts((now) => now.map((d, i) => (i === index ? next : d)))}
          onRemove={() => setDrafts((now) => now.filter((_, i) => i !== index))}
        />
      ))}
      <div className="import-actions">
        {data && drafts.length === 0 && data.usual.length > 0 && (
          <button
            type="button"
            className="action"
            onClick={() => setDrafts(data.usual.map(toDraft))}
          >
            {t("sizes.start_usual")}
          </button>
        )}
        {drafts.length < 6 && (
          <button
            type="button"
            className="action"
            onClick={() => setDrafts((now) => [...now, EMPTY])}
          >
            {t("sizes.add")}
          </button>
        )}
        <button
          type="button"
          className="button button-ink button-small"
          disabled={state === "saving" || !data}
          onClick={() => void save()}
        >
          {t("sizes.save")}
        </button>
        {status[state] && (
          <span role={state === "failed" ? "alert" : "status"}>{t(status[state])}</span>
        )}
      </div>
    </section>
  );
}
