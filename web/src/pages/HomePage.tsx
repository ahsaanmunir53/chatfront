import type { CSSProperties } from "react";
import { Link } from "react-router-dom";
import { Avatar } from "../components/Avatar";
import { ArrowIcon, CheckIcon } from "../components/Icons";
import { Footer, TopBar } from "../components/Shell";
import { DEMO_SHOPS } from "../demo";
import { pick, scriptLang } from "../i18n";
import { useLang } from "../lang";

const CHANNELS = ["WhatsApp", "Instagram", "Messenger"];

const delay = (seconds: number) => ({ "--d": `${seconds}s` }) as CSSProperties;

/** The hero's phone: one order from question to verified money, played once on load. */
function HeroChat() {
  const { t } = useLang();
  return (
    <div className="hero-phone" aria-hidden="true">
      <div className="hero-phone-bar">
        <Avatar name="Gulmohar Lawn House" template="clothing" size="sm" />
        <span className="hero-phone-name">
          Gulmohar Lawn House
          <small>{t("chat.online")}</small>
        </span>
      </div>
      <div className="hero-thread">
        <p className="hero-msg hero-in" style={delay(0.3)}>
          {t("demo.ask")}
        </p>
        <p className="hero-msg hero-out" style={delay(1.2)}>
          {t("demo.price")}
        </p>
        <div className="hero-msg hero-in hero-proof" style={delay(2.3)}>
          <span className="shot">
            <span className="shot-text">
              <span className="shot-label">{t("demo.screenshot")}</span>
              <span className="shot-amount" lang="en">
                Rs 4,850
              </span>
              <span className="shot-meta" lang="en">
                TID ••• 4821, 11:42
              </span>
            </span>
            <span className="stamp">
              <CheckIcon size={16} />
              {t("demo.stamp")}
            </span>
          </span>
          {t("demo.paid")}
        </div>
        <p className="hero-msg hero-system" style={delay(3.2)}>
          {t("demo.matching")}
        </p>
        <p className="hero-msg hero-out hero-receipt" style={delay(4.9)}>
          <CheckIcon size={16} />
          {t("demo.receipt")}
        </p>
      </div>
    </div>
  );
}

function Proof() {
  const { t } = useLang();
  const rows: Array<[string, string, string]> = [
    [t("proof.amount"), "Rs 4,850", "Rs 4,850.00"],
    [t("proof.tid"), "0123 4567 4821", "0123 4567 4821"],
    [t("proof.time"), "11:42", "11:42:07"],
  ];
  return (
    <section className="band band-paper" id="proof" aria-labelledby="proof-title">
      <div className="band-inner proof">
        <div className="proof-copy">
          <h2 id="proof-title" className="title">
            {t("proof.title")}
          </h2>
          <p className="lede">{t("proof.lede")}</p>
        </div>
        <div className="ledger" role="table" aria-label={t("proof.title")}>
          <div className="ledger-row ledger-head" role="row">
            <span role="columnheader" />
            <span role="columnheader">{t("proof.claimed")}</span>
            <span role="columnheader">{t("proof.arrived")}</span>
          </div>
          {rows.map(([label, claimed, arrived]) => (
            <div className="ledger-row" role="row" key={label}>
              <span role="rowheader">{label}</span>
              <span role="cell" lang="en">
                {claimed}
              </span>
              <span role="cell" lang="en" className="ledger-match">
                {arrived}
                <CheckIcon size={16} />
              </span>
            </div>
          ))}
          <p className="ledger-verdict">{t("proof.verdict")}</p>
        </div>
      </div>
    </section>
  );
}

function HowItWorks() {
  const { t } = useLang();
  const steps = [1, 2, 3] as const;
  return (
    <section className="band band-plain" aria-labelledby="how-title">
      <div className="band-inner">
        <h2 id="how-title" className="title">
          {t("how.title")}
        </h2>
        <ol className="steps">
          {steps.map((n) => (
            <li key={n} className="step">
              <span className="step-number" aria-hidden="true">
                {n}
              </span>
              <h3>{t(`how.${n}_title` as const)}</h3>
              <p>{t(`how.${n}_body` as const)}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function Shops() {
  const { lang, t } = useLang();
  return (
    <section className="band band-paper" id="shops" aria-labelledby="shops-title">
      <div className="band-inner">
        <h2 id="shops-title" className="title">
          {t("home.demo_shops")}
        </h2>
        <ul className="shop-grid">
          {DEMO_SHOPS.map((shop) => (
            <li key={shop.slug}>
              <Link to={`/s/${shop.slug}`} className="shop-card">
                <Avatar name={shop.name} template={shop.template} />
                <span className="shop-card-text">
                  <span className="shop-card-name" lang={scriptLang(shop.name)}>
                    {shop.name}
                  </span>
                  <span className="shop-card-kind">
                    {pick(shop.business_type, lang)}, {shop.city}
                  </span>
                  <span className="shop-card-tagline">{pick(shop.tagline, lang)}</span>
                </span>
                <ArrowIcon className="shop-card-arrow flip-rtl" size={18} />
              </Link>
            </li>
          ))}
        </ul>
        <p className="aside">{t("home.demo_note")}</p>
      </div>
    </section>
  );
}

export function HomePage() {
  const { lang, t } = useLang();
  return (
    <div className="page">
      <div className="hero">
        <TopBar tone="dark" />
        <div className="hero-inner">
          <div className="hero-copy">
            {lang !== "ur" && (
              <p className="hero-urdu" lang="ur">
                {t("landing.urdu_line")}
              </p>
            )}
            <h1 className="hero-title">{t("landing.title")}</h1>
            <p className="hero-lede">{t("landing.lede")}</p>
            <ul className="channel-chips">
              {CHANNELS.map((name) => (
                <li key={name} lang="en">
                  {name}
                </li>
              ))}
            </ul>
            <div className="hero-actions">
              <a href="#shops" className="button button-saffron">
                {t("home.demo_shops")}
              </a>
              <Link to="/start" className="button button-ghost">
                {t("home.make_shop")}
              </Link>
            </div>
          </div>
          <HeroChat />
        </div>
      </div>
      <main>
        <Proof />
        <HowItWorks />
        <Shops />
      </main>
      <Footer />
    </div>
  );
}
