import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Problem, Shell } from "../components/Shell";
import { pick, scriptLang } from "../i18n";
import { useLang } from "../lang";
import { useLoad } from "../useLoad";

export function ShopPage() {
  const { slug = "" } = useParams();
  const { lang, t } = useLang();
  const shop = useLoad(() => api.shop(slug), slug);

  if (shop.state === "missing" || shop.state === "failed") {
    return (
      <Shell>
        <Problem message={t(shop.state === "missing" ? "shop.not_found" : "error.network")} />
      </Shell>
    );
  }
  if (shop.state === "loading") return <Shell>{null}</Shell>;

  const { data } = shop;
  return (
    <Shell>
      <section className="hero">
        <p className="kind">
          {pick(data.business_type, lang)}
          {data.city ? `, ${data.city}` : ""}
        </p>
        <h1 className="display" lang={scriptLang(data.name)}>
          {data.name}
        </h1>
        <p className="lede">{pick(data.tagline, lang)}</p>
        <div className="actions">
          <Link to={`/s/${slug}/chat`} className="button">
            {t("shop.chat_cta")}
          </Link>
          <Link to={`/s/${slug}/trust`} className="quiet-link">
            {t("shop.trust_link")}
          </Link>
        </div>
      </section>
      <section aria-labelledby="products-heading">
        <h2 id="products-heading" className="section-title">
          {t("shop.products")}
        </h2>
        <p className="empty">{t("shop.empty")}</p>
      </section>
    </Shell>
  );
}
