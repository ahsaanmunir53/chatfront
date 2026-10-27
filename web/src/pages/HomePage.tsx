import { Link } from "react-router-dom";
import { api } from "../api";
import { Problem, Shell } from "../components/Shell";
import { pick, scriptLang } from "../i18n";
import { useLang } from "../lang";
import { useLoad } from "../useLoad";

export function HomePage() {
  const { lang, t } = useLang();
  const shops = useLoad(api.demoShops, "demo-shops");
  return (
    <Shell>
      <section className="hero">
        <h1 className="display">{t("app.name")}</h1>
        <p className="lede">{t("home.lede")}</p>
      </section>
      <section aria-labelledby="demo-heading">
        <h2 id="demo-heading" className="section-title">
          {t("home.demo_shops")}
        </h2>
        {shops.state === "failed" && <Problem message={t("error.network")} />}
        {shops.state === "ready" && (
          <ul className="entries">
            {shops.data.map((shop) => (
              <li key={shop.slug} className="entry">
                <Link to={`/s/${shop.slug}`} className="entry-link">
                  <span className="entry-name" lang={scriptLang(shop.name)}>
                    {shop.name}
                  </span>
                  <span className="entry-kind">{pick(shop.business_type, lang)}</span>
                  <span className="entry-note">{pick(shop.tagline, lang)}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
        <p className="aside">{t("home.demo_note")}</p>
      </section>
    </Shell>
  );
}
