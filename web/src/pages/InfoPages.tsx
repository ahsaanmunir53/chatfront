import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useParams } from "react-router-dom";
import { ApiError, api } from "../api";
import { useCopy } from "../clipboard";
import { API_BASE } from "../config";
import type { OfficialResult, TrustInfo, TrustStats } from "../api";
import { Avatar } from "../components/Avatar";
import { ShieldIcon } from "../components/Icons";
import { ShopReviewsSection } from "../components/Reviews";
import { Page, Problem } from "../components/Shell";
import { demoShop } from "../demo";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { useServer } from "../server";

export function TrustPage() {
  const { slug = "" } = useParams();
  const { t } = useLang();
  const { state } = useServer();
  const bundled = demoShop(slug);
  const [trust, setTrust] = useState<TrustInfo | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (state !== "online") return;
    let current = true;
    api.trust(slug).then(
      (info) => current && setTrust(info),
      (error: unknown) => {
        if (current && error instanceof ApiError && error.status === 404) setMissing(true);
      },
    );
    return () => {
      current = false;
    };
  }, [slug, state]);

  const name = trust?.name ?? bundled?.name;
  if (missing || (!name && state === "offline")) {
    return (
      <Page>
        <Problem message={t("shop.not_found")} />
      </Page>
    );
  }
  return (
    <Page>
      <section className="plain-hero">
        <ShieldIcon size={36} className="plain-hero-icon" />
        <h1 className="title">{t("trust.title")}</h1>
        {name && <p className="lede">{t("trust.lede", { shop: name })}</p>}
      </section>
      <section aria-labelledby="channels-heading" className="plain-section">
        <h2 id="channels-heading" className="section-title">
          {t("trust.channels")}
        </h2>
        <ul className="channel-list" aria-busy={!trust}>
          {trust?.channels.map((c) => (
            <li key={`${c.channel}:${c.display}`}>
              {name && <Avatar name={name} template={bundled?.template} size="sm" />}
              <span className="channel-list-text">
                <span className="channel-list-name">{t(`channel.${c.channel}` as Key)}</span>
                {c.channel !== "webchat" && c.display && (
                  <span className="channel-list-handle" dir="ltr">
                    {c.display}
                  </span>
                )}
              </span>
              {c.url && (
                <a href={c.url} className="button button-ink button-small" rel="noopener">
                  {t("shop.chat_here")}
                </a>
              )}
            </li>
          ))}
        </ul>
      </section>
      {name && <OfficialCheck slug={slug} shop={name} />}
      <TrustNumbers slug={slug} />
      <ShopReviewsSection slug={slug} />
      <AssistantAccess slug={slug} />
    </Page>
  );
}

/** "Is this number really the shop's?" (feature 41): paste the number or account that wrote
 * to you; only the shop's connected channels pass. */
function OfficialCheck({ slug, shop }: { slug: string; shop: string }) {
  const { t } = useLang();
  const [value, setValue] = useState("");
  const [result, setResult] = useState<OfficialResult | "checking" | "failed" | null>(null);
  async function check(event: FormEvent) {
    event.preventDefault();
    if (!value.trim()) return;
    setResult("checking");
    try {
      setResult(await api.checkOfficial(slug, value.trim()));
    } catch {
      setResult("failed");
    }
  }
  return (
    <section aria-labelledby="official-heading" className="plain-section official-check">
      <h2 id="official-heading" className="section-title">
        {t("trust.check_title")}
      </h2>
      <p className="lede">{t("trust.check_lede", { shop })}</p>
      <form className="official-form" onSubmit={(event) => void check(event)}>
        <label>
          <span>{t("trust.check_label")}</span>
          <input
            value={value}
            maxLength={120}
            dir="ltr"
            autoComplete="off"
            onChange={(e) => {
              setValue(e.target.value);
              setResult(null);
            }}
          />
        </label>
        <button
          type="submit"
          className="button button-ink button-small"
          disabled={result === "checking"}
        >
          {t("trust.check_button")}
        </button>
      </form>
      {result === "failed" && <p role="alert">{t("trust.check_failed")}</p>}
      {result !== null && typeof result === "object" && (
        <p role="status" className={result.official ? "pay-on" : "order-failed"}>
          {result.official
            ? t("trust.check_yes", { shop, channel: t(`channel.${result.channel}` as Key) })
            : t("trust.check_no", { shop })}
        </p>
      )}
    </section>
  );
}

/** The shop's address for AI assistants that speak MCP (feature 66): they can look up its
 * products and start a checkout, which the person places themselves. */
function AssistantAccess({ slug }: { slug: string }) {
  const { t } = useLang();
  const { copied, copy } = useCopy();
  const url = `${API_BASE || window.location.origin}/mcp/${slug}`;
  return (
    <section aria-labelledby="mcp-heading" className="plain-section assistant-access">
      <h2 id="mcp-heading" className="section-title">
        {t("mcp.title")}
      </h2>
      <p className="lede">{t("mcp.lede")}</p>
      <p className="mcp-url">
        <code dir="ltr">{url}</code>
        <button type="button" className="action" onClick={() => void copy(url)}>
          {copied ? t("mcp.copied") : t("mcp.copy")}
        </button>
      </p>
    </section>
  );
}

/** What the shop's own records say (feature 45); a figure waits until there is enough
 * behind it. */
function TrustNumbers({ slug }: { slug: string }) {
  const { t, lang } = useLang();
  const { state } = useServer();
  const [stats, setStats] = useState<TrustStats | null>(null);
  useEffect(() => {
    if (state !== "online") return;
    let current = true;
    api.trustStats(slug).then(
      (found) => current && setStats(found),
      () => {
        // The page still shows the shop's channels.
      },
    );
    return () => {
      current = false;
    };
  }, [slug, state]);
  if (!stats) return null;
  const since = new Intl.DateTimeFormat(lang, { month: "long", year: "numeric" }).format(
    new Date(stats.since),
  );
  const lines: string[] = [
    t("trust.since", { date: since }),
    t("trust.delivered", { count: stats.delivered }),
    stats.return_rate === null
      ? t("trust.returns_new")
      : t("trust.returns", { rate: stats.return_rate }),
  ];
  if (stats.reply_minutes !== null) lines.push(t("trust.reply", { minutes: stats.reply_minutes }));
  if (stats.rating !== null) {
    lines.push(t("trust.rating", { rating: stats.rating.toFixed(1), count: stats.reviews }));
  }
  return (
    <section aria-labelledby="numbers-heading" className="plain-section">
      <h2 id="numbers-heading" className="section-title">
        {t("trust.numbers")}
      </h2>
      <ul className="trust-numbers">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <p className="pay-note">{t("trust.numbers_note")}</p>
    </section>
  );
}

export function NotFoundPage() {
  const { t } = useLang();
  return (
    <Page>
      <Problem message={t("error.not_found")} />
    </Page>
  );
}
