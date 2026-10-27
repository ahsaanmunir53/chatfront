import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../api";
import type { ChannelLink } from "../api";
import { Avatar } from "../components/Avatar";
import { ChatIcon, PinIcon, SendIcon } from "../components/Icons";
import { Page, Problem } from "../components/Shell";
import { demoShop } from "../demo";
import { pick, scriptLang } from "../i18n";
import type { Text3 } from "../i18n";
import { useLang } from "../lang";
import { useServer } from "../server";

type Profile = {
  name: string;
  template?: string;
  business_type: Text3;
  city: string | null;
  tagline: Text3;
};

// WhatsApp first: that is where most Pakistani buyers already talk to shops.
const CHANNEL_NAMES: Record<string, string> = {
  whatsapp: "WhatsApp",
  instagram: "Instagram",
  messenger: "Messenger",
};
const CHANNEL_ORDER = Object.keys(CHANNEL_NAMES);

/** The shop's profile: bundled for demo shops so it opens at once, then filled in live. */
export function useShop(slug: string) {
  const { state } = useServer();
  const bundled = demoShop(slug);
  const [live, setLive] = useState<{ profile: Profile; channels: ChannelLink[] } | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (state !== "online") return;
    let current = true;
    api.shop(slug).then(
      (shop) => current && setLive({ profile: shop, channels: shop.channels }),
      (error: unknown) => {
        if (current && error instanceof ApiError && error.status === 404) setMissing(true);
      },
    );
    return () => {
      current = false;
    };
  }, [slug, state]);

  const profile: Profile | undefined = live?.profile ?? bundled;
  return {
    profile: profile && { ...profile, template: bundled?.template ?? profile.template },
    channels: live?.channels ?? [],
    missing: missing && !bundled,
  };
}

export function ShopPage() {
  const { slug = "" } = useParams();
  const { lang, t } = useLang();
  const { profile, channels, missing } = useShop(slug);

  if (missing) {
    return (
      <Page>
        <Problem message={t("shop.not_found")} />
      </Page>
    );
  }
  if (!profile) {
    return (
      <Page>
        <div className="profile-skeleton" aria-busy="true" />
      </Page>
    );
  }

  const outside = channels
    .filter((c) => c.url && c.channel in CHANNEL_NAMES)
    .sort((a, b) => CHANNEL_ORDER.indexOf(a.channel) - CHANNEL_ORDER.indexOf(b.channel));
  return (
    <Page>
      <section className="profile">
        <div className="profile-cover" aria-hidden="true" />
        <div className="profile-body">
          <Avatar name={profile.name} template={profile.template} size="lg" />
          <h1 className="profile-name" lang={scriptLang(profile.name)} dir="auto">
            {profile.name}
          </h1>
          <p className="profile-kind">
            {pick(profile.business_type, lang)}
            {profile.city && (
              <span className="profile-city">
                <PinIcon size={16} />
                {profile.city}
              </span>
            )}
          </p>
          <p className="profile-tagline">{pick(profile.tagline, lang)}</p>
          <div className="profile-actions">
            <Link to={`/s/${slug}/chat`} className="action action-primary">
              <ChatIcon />
              {t("shop.chat_here")}
            </Link>
            {outside.length > 0 && (
              <div className="profile-channels">
                {outside.map((c) => (
                  <a key={c.channel} href={c.url ?? undefined} className="action" rel="noopener">
                    <SendIcon className="flip-rtl" />
                    <span lang="en">{CHANNEL_NAMES[c.channel]}</span>
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="catalog" aria-labelledby="catalog-title">
        <h2 id="catalog-title" className="section-title">
          {t("shop.products")}
        </h2>
        <div className="catalog-empty">
          <span className="catalog-ghost" aria-hidden="true" />
          <span className="catalog-ghost" aria-hidden="true" />
          <span className="catalog-ghost" aria-hidden="true" />
          <p>{t("shop.catalog_soon")}</p>
          <Link to={`/s/${slug}/chat`} className="button button-ink">
            {t("shop.chat_here")}
          </Link>
        </div>
      </section>

      <p className="trust-link">
        <Link to={`/s/${slug}/trust`}>{t("shop.trust_link")}</Link>
      </p>
    </Page>
  );
}
