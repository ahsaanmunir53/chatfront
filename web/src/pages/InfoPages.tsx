import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, api } from "../api";
import type { TrustInfo } from "../api";
import { Avatar } from "../components/Avatar";
import { ShieldIcon } from "../components/Icons";
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
    </Page>
  );
}

function Explainer({ title, lede }: { title: Key; lede: Key }) {
  const { t } = useLang();
  return (
    <Page>
      <section className="plain-hero">
        <h1 className="title">{t(title)}</h1>
        <p className="lede">{t(lede)}</p>
      </section>
    </Page>
  );
}

export const DashboardPage = () => <Explainer title="dashboard.title" lede="dashboard.lede" />;

export function NotFoundPage() {
  const { t } = useLang();
  return (
    <Page>
      <Problem message={t("error.not_found")} />
    </Page>
  );
}
