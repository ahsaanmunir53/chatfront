// The dashboard at the top of the console (features 74, 112, 113): orders, money, fraud
// checks, the books and what the model cost, for today, 7 days or 30 days. One period switch
// scopes every number below it; each part shows only when the person's role may see it.
// Every figure comes from the API; nothing is added up here.
import { useCallback, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import type { Dashboard, DashboardDays, OwnerMe } from "../api";
import { languageOf, localeOf } from "../i18n";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { formatMoney } from "../money";
import { usePolled } from "../usePolled";

const POLL_MS = 15000;
const PERIODS: DashboardDays[] = [1, 7, 30];
const PERIOD_KEY: Record<DashboardDays, Key> = {
  1: "console.dash_today",
  7: "console.dash_7",
  30: "console.dash_30",
};
const TO_PACK = ["placed", "paid", "confirmed"];
const EFFORTS = ["low", "medium", "high", "xhigh"] as const;
// The model's costs are counted in Pakistani rupees, whatever the shop's own currency.
const COST_COUNTRY = "PK";

type Row = { key: Key; value: string; values?: Record<string, string | number> };

/** Formats the console needs: money, counts and the meter's thousandths of a paisa. */
function useFormats(me: OwnerMe) {
  const { t, lang } = useLang();
  const locale = localeOf(languageOf(lang));
  const counter = new Intl.NumberFormat(locale);
  const compact = new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 });
  const money = (minor: number) => formatMoney(minor, me.country, lang);
  const cost = (millipaisa: number) => {
    const paisa = Math.round(millipaisa / 1000);
    if (millipaisa > 0 && paisa === 0) {
      return t("console.dash_under", { amount: formatMoney(1, COST_COUNTRY, lang) });
    }
    return formatMoney(paisa, COST_COUNTRY, lang);
  };
  return { t, lang, money, cost, count: counter.format, compact: compact.format };
}

function Ledger({ title, rows, note }: { title: string; rows: Row[]; note?: ReactNode }) {
  const { t } = useLang();
  return (
    <div className="pay-block dash-card">
      <h3 className="pay-block-title">{title}</h3>
      <dl className="dash-rows">
        {rows.map((row) => (
          <div key={row.key}>
            <dt>{t(row.key, row.values)}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
      {note}
    </div>
  );
}

/** The width an element is drawn at, so a chart draws at its real size and its text stays
 * readable on a phone instead of shrinking with the picture. */
function useWidth(
  initial: number,
): [(node: HTMLDivElement | null) => (() => void) | undefined, number] {
  const [width, setWidth] = useState(initial);
  const box = useCallback((node: HTMLDivElement | null) => {
    if (!node || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const seen = Math.round(entry?.contentRect.width ?? 0);
      if (seen > 0) setWidth(seen);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return [box, width];
}

function OrdersPerDay({ me, data }: { me: OwnerMe; data: NonNullable<Dashboard["daily"]> }) {
  const { t, lang, money, count } = useFormats(me);
  const [focus, setFocus] = useState<number | null>(null);
  const most = Math.max(1, ...data.map((day) => day.orders));
  const top = most <= 4 ? most : Math.ceil(most / 5) * 5;
  const ticks = top <= 4 ? Array.from({ length: top + 1 }, (_, n) => n) : [0, top / 2, top];
  const [box, width] = useWidth(640);
  const height = 180;
  const left = 32;
  const bottom = 24;
  const plot = height - bottom - 8;
  const band = (width - left) / data.length;
  const bar = Math.min(24, band - 4);
  const label = (iso: string) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" });
  const every = Math.ceil(data.length / Math.max(2, Math.floor(width / 72)));
  const shown = focus === null ? null : data[focus];

  return (
    <figure className="dash-chart">
      <figcaption className="pay-block-title">{t("console.dash_chart")}</figcaption>
      <div className="dash-plot" ref={box}>
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("console.dash_chart")}>
          {ticks.map((tick) => {
            const y = 8 + plot - (tick / top) * plot;
            return (
              <g key={tick} className="dash-grid">
                <line x1={left} x2={width} y1={y} y2={y} />
                <text x={left - 6} y={y + 4} textAnchor="end">
                  {count(tick)}
                </text>
              </g>
            );
          })}
          {data.map((day, index) => {
            const x = left + index * band + (band - bar) / 2;
            const h = (day.orders / top) * plot;
            const y = 8 + plot - h;
            const r = Math.min(4, h / 2);
            return (
              <g key={day.day}>
                {h > 0 && (
                  <path
                    className={`dash-bar${focus === index ? " is-focus" : ""}`}
                    d={`M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + bar - r} Q${x + bar},${y} ${x + bar},${y + r} V${y + h} Z`}
                  />
                )}
                {index % every === (data.length - 1) % every && (
                  <text className="dash-axis" x={x + bar / 2} y={height - 6} textAnchor="middle">
                    {label(day.day)}
                  </text>
                )}
                <rect
                  className="dash-hit"
                  x={left + index * band}
                  y={0}
                  width={band}
                  height={height - bottom}
                  tabIndex={0}
                  aria-label={`${label(day.day)}: ${t("console.dash_chart_tip", { orders: day.orders })}`}
                  onPointerEnter={() => setFocus(index)}
                  onPointerLeave={() => setFocus(null)}
                  onFocus={() => setFocus(index)}
                  onBlur={() => setFocus(null)}
                />
              </g>
            );
          })}
          <line className="dash-base" x1={left} x2={width} y1={8 + plot} y2={8 + plot} />
        </svg>
        {shown && focus !== null && (
          <div
            className="dash-tip"
            role="status"
            style={{ insetInlineStart: `${((left + focus * band + band / 2) / width) * 100}%` }}
          >
            <strong>{t("console.dash_chart_tip", { orders: shown.orders })}</strong>
            {shown.sales !== null && (
              <span>{t("console.dash_chart_sales", { amount: money(shown.sales) })}</span>
            )}
            <span>{label(shown.day)}</span>
          </div>
        )}
      </div>
      <details className="dash-table">
        <summary>{t("console.dash_table")}</summary>
        <table className="daybook">
          <thead>
            <tr>
              <th scope="col">{t("console.dash_day")}</th>
              <th scope="col">{t("console.dash_orders")}</th>
              {data[0]?.sales !== null && <th scope="col">{t("console.books_sales")}</th>}
            </tr>
          </thead>
          <tbody>
            {data.map((day) => (
              <tr key={day.day}>
                <th scope="row">{label(day.day)}</th>
                <td>{count(day.orders)}</td>
                {day.sales !== null && <td>{money(day.sales)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

function EffortBar({ efforts }: { efforts: Record<string, number> }) {
  const { t } = useLang();
  const total = EFFORTS.reduce((sum, effort) => sum + (efforts[effort] ?? 0), 0);
  if (total === 0) return null;
  return (
    <div className="dash-effort">
      <p className="dash-effort-title">{t("console.dash_effort")}</p>
      <div className="dash-effort-bar" aria-hidden="true">
        {EFFORTS.filter((effort) => efforts[effort]).map((effort) => (
          <span
            key={effort}
            className={`effort-${effort}`}
            style={{ flexGrow: efforts[effort] ?? 0 }}
          />
        ))}
      </div>
      <ul className="dash-effort-key">
        {EFFORTS.map((effort) => (
          <li key={effort}>
            <span className={`dash-swatch effort-${effort}`} aria-hidden="true" />
            {t(`console.dash_effort.${effort}` as Key)}
            <strong>{efforts[effort] ?? 0}</strong>
          </li>
        ))}
      </ul>
      <p className="pay-note">{t("console.dash_effort_note")}</p>
    </div>
  );
}

function ModelCard({ me, ai }: { me: OwnerMe; ai: NonNullable<Dashboard["ai"]> }) {
  const { t, cost, count, compact } = useFormats(me);
  const rows: Row[] = [
    { key: "console.dash_calls", value: count(ai.calls) },
    { key: "console.dash_tokens", value: compact(ai.tokens) },
    { key: "console.dash_cost", value: cost(ai.cost_millipaisa) },
    {
      key: "console.dash_per_order",
      value: ai.per_order_millipaisa === null ? "-" : cost(ai.per_order_millipaisa),
    },
  ];
  if (ai.failed > 0) rows.push({ key: "console.dash_failed", value: count(ai.failed) });
  return (
    <Ledger
      title={t("console.dash_model")}
      rows={rows}
      note={
        <>
          {!ai.live && <p className="pay-note">{t("console.dash_mock")}</p>}
          <EffortBar efforts={ai.efforts} />
        </>
      }
    />
  );
}

function OrderCosts({ me, costs }: { me: OwnerMe; costs: NonNullable<Dashboard["costs"]> }) {
  const { t, money, cost, count, compact } = useFormats(me);
  return (
    <div className="pay-block dash-costs">
      <h3 className="pay-block-title">{t("console.dash_costs")}</h3>
      {costs.length === 0 ? (
        <p className="aside">{t("console.dash_costs_none")}</p>
      ) : (
        <table className="daybook">
          <thead>
            <tr>
              <th scope="col">{t("console.dash_order")}</th>
              <th scope="col">{t("console.dash_total")}</th>
              <th scope="col">{t("console.dash_calls")}</th>
              <th scope="col">{t("console.dash_tokens")}</th>
              <th scope="col">{t("console.dash_cost")}</th>
            </tr>
          </thead>
          <tbody>
            {costs.map((row) => (
              <tr key={row.code}>
                <th scope="row">
                  <span dir="ltr">{row.code}</span>{" "}
                  <span className="dash-status">{t(`order.status.${row.status}` as Key)}</span>
                </th>
                <td>{money(row.total)}</td>
                <td>{count(row.calls)}</td>
                <td>{compact(row.tokens)}</td>
                <td>{cost(row.cost_millipaisa)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function sum(record: Record<string, number>, keys: string[]): number {
  return keys.reduce((total, key) => total + (record[key] ?? 0), 0);
}

function Cards({ me, data }: { me: OwnerMe; data: Dashboard }) {
  const { t, money, count } = useFormats(me);
  const { orders, money: cash, fraud, books } = data;
  return (
    <div className="dash-cards">
      {orders && (
        <Ledger
          title={t("console.dash_orders")}
          rows={[
            {
              key: "console.dash_awaiting_payment",
              value: count(orders.open.awaiting_payment ?? 0),
            },
            { key: "console.dash_to_pack", value: count(sum(orders.open, TO_PACK)) },
            { key: "console.dash_packed", value: count(orders.open.packed ?? 0) },
            { key: "console.dash_dispatched", value: count(orders.open.dispatched ?? 0) },
            { key: "console.dash_delivered", value: count(orders.closed.delivered ?? 0) },
            {
              key: "console.dash_returned",
              value: count(sum(orders.closed, ["returned", "cancelled"])),
            },
          ]}
          note={
            Object.keys(orders.sources).length > 0 && (
              <p className="dash-sources">
                {Object.entries(orders.sources).map(([source, n]) => (
                  <span key={source}>
                    {t(`console.dash_source.${source}` as Key)} <strong>{count(n)}</strong>
                  </span>
                ))}
              </p>
            )
          }
        />
      )}
      {cash && (
        <Ledger
          title={t("console.dash_money")}
          rows={[
            {
              key: "console.dash_received",
              values: { count: cash.payments },
              value: money(cash.received),
            },
            {
              key: "console.dash_awaiting",
              values: { count: cash.awaiting_orders },
              value: money(cash.awaiting),
            },
            { key: "console.dash_at_doors", value: money(cash.at_doors) },
            { key: "console.dash_to_review", value: count(cash.to_review) },
          ]}
        />
      )}
      {fraud && (
        <Ledger
          title={t("console.dash_fraud")}
          rows={[
            { key: "console.dash_screenshots", value: count(fraud.screenshots) },
            { key: "console.dash_flagged", value: count(fraud.flagged) },
            { key: "console.dash_reused", value: count(fraud.reused) },
            { key: "console.dash_rejected", value: count(fraud.rejected) },
            { key: "console.dash_risky", value: count(fraud.risky_orders) },
            { key: "console.dash_advances", value: count(fraud.advances) },
          ]}
        />
      )}
      {books && (
        <Ledger
          title={t("console.books_title")}
          rows={[
            { key: "console.books_sales", value: money(books.sales) },
            { key: "console.books_expenses", value: money(books.expenses) },
            { key: "console.books_profit", value: money(books.cash_profit) },
            { key: "console.books_goods_profit", value: money(books.goods_profit) },
            { key: "console.books_write_offs", value: money(books.write_offs) },
            { key: "console.dash_udhaar_owed", value: money(books.udhaar_owed) },
          ]}
        />
      )}
    </div>
  );
}

export function DashboardPanel({ me, token }: { me: OwnerMe; token: string }) {
  const { t, money, count } = useFormats(me);
  const [days, setDays] = useState<DashboardDays>(7);
  const load = useCallback(() => api.dashboard(token, days), [token, days]);
  const { data } = usePolled(load, POLL_MS);
  const current = data && data.period.days === days ? data : null;

  return (
    <section className="dashboard" aria-labelledby="dashboard-title">
      <div className="dash-head">
        <h2 id="dashboard-title" className="section-title">
          {t("console.dash_title")}
        </h2>
        <div
          className="khata-kinds dash-periods"
          role="radiogroup"
          aria-label={t("console.dash_period")}
        >
          {PERIODS.map((period) => (
            <button
              key={period}
              type="button"
              role="radio"
              aria-checked={period === days}
              onClick={() => setDays(period)}
            >
              {t(PERIOD_KEY[period])}
            </button>
          ))}
        </div>
      </div>
      {data && (
        <div className={`dash-body${current ? "" : " is-loading"}`} aria-busy={!current}>
          {(data.orders || data.daily) && (
            <div className="dash-lead">
              {data.orders && (
                <p className="dash-hero">
                  <span className="dash-hero-number">{count(data.orders.placed)}</span>
                  <span className="dash-hero-label">
                    {t("console.dash_placed", { count: data.orders.placed })}
                  </span>
                  <span className="dash-hero-worth">
                    {t("console.dash_worth", { amount: money(data.orders.value) })}
                  </span>
                </p>
              )}
              {data.daily && <OrdersPerDay me={me} data={data.daily} />}
            </div>
          )}
          <Cards me={me} data={data} />
          {data.ai && (
            <div className="dash-model">
              <ModelCard me={me} ai={data.ai} />
              {data.costs && <OrderCosts me={me} costs={data.costs} />}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
