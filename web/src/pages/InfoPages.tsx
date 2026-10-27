import { useParams } from "react-router-dom";
import { api } from "../api";
import { Problem, Shell } from "../components/Shell";
import type { Key } from "../i18n";
import { useLang } from "../lang";
import { useLoad } from "../useLoad";

export function TrustPage() {
  const { slug = "" } = useParams();
  const { t } = useLang();
  const trust = useLoad(() => api.trust(slug), slug);
  if (trust.state === "missing" || trust.state === "failed") {
    return (
      <Shell>
        <Problem message={t(trust.state === "missing" ? "shop.not_found" : "error.network")} />
      </Shell>
    );
  }
  return (
    <Shell>
      <section className="hero">
        <h1 className="display display-small">{t("trust.title")}</h1>
        {trust.state === "ready" && (
          <p className="lede">{t("trust.lede", { shop: trust.data.name })}</p>
        )}
      </section>
      {trust.state === "ready" && (
        <section aria-labelledby="channels-heading">
          <h2 id="channels-heading" className="section-title">
            {t("trust.channels")}
          </h2>
          <ul className="entries">
            {trust.data.channels.map((c) => (
              <li key={`${c.channel}:${c.display}`} className="entry entry-static">
                <span className="entry-name">{t(`channel.${c.channel}` as Key)}</span>
                {c.channel !== "webchat" && c.display && (
                  <span className="entry-note" dir="ltr">
                    {c.display}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </Shell>
  );
}

function Explainer({ title, lede }: { title: Key; lede: Key }) {
  const { t } = useLang();
  return (
    <Shell>
      <section className="hero">
        <h1 className="display display-small">{t(title)}</h1>
        <p className="lede">{t(lede)}</p>
      </section>
    </Shell>
  );
}

export const DashboardPage = () => <Explainer title="dashboard.title" lede="dashboard.lede" />;
export const ReceiptPage = () => <Explainer title="receipt.title" lede="receipt.lede" />;

export function NotFoundPage() {
  const { t } = useLang();
  return (
    <Shell>
      <Problem message={t("error.not_found")} />
    </Shell>
  );
}
