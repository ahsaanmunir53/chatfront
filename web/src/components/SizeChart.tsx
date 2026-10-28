// A product's size chart on the shop page (feature 5): each size's range per body measure.
import type { Measure, SizeChart } from "../api";
import type { Key } from "../i18n";
import { useLang } from "../lang";

export const MEASURES: Measure[] = [
  "chest",
  "waist",
  "hips",
  "shoulder",
  "length",
  "sleeve",
  "inseam",
  "foot",
];

export const MEASURE_LABEL: Record<Measure, Key> = {
  chest: "size.chest",
  waist: "size.waist",
  hips: "size.hips",
  shoulder: "size.shoulder",
  length: "size.length",
  sleeve: "size.sleeve",
  inseam: "size.inseam",
  foot: "size.foot",
};

/** The measures a chart lists, in the usual order. */
export function measuresOf(chart: SizeChart): Measure[] {
  return MEASURES.filter((measure) => chart.rows.some((row) => row.ranges[measure]));
}

function range(value: [number, number] | undefined): string {
  if (!value) return "–";
  return value[0] === value[1] ? `${value[0]}` : `${value[0]}–${value[1]}`;
}

export function SizeChartTable({ chart }: { chart: SizeChart }) {
  const { t } = useLang();
  const measures = measuresOf(chart);
  const unit = t(chart.unit === "cm" ? "size.unit_cm" : "size.unit_in");
  return (
    <details className="size-chart">
      <summary>{t("shop.size_chart")}</summary>
      <p className="size-chart-help">{t("shop.size_chart_help", { unit })}</p>
      <div className="size-chart-scroll">
        <table aria-label={t("shop.size_chart")}>
          <thead>
            <tr>
              <th scope="col">{t("size.size")}</th>
              {measures.map((measure) => (
                <th key={measure} scope="col">
                  {t(MEASURE_LABEL[measure])}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {chart.rows.map((row) => (
              <tr key={row.size}>
                <th scope="row" dir="auto">
                  {row.size}
                </th>
                {measures.map((measure) => (
                  <td key={measure} dir="ltr">
                    {range(row.ranges[measure])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
