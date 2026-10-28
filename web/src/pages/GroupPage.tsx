// A group order's page (feature 22), opened from the link the organiser shares: who has added
// a part, how many pieces and whether it is paid, and when it closes. Only first names show,
// never an address or a number. A friend adds a part from the shop page; the organiser can
// close it early, and then every part is packed together.
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api } from "../api";
import type { GroupPart, GroupView } from "../api";
import { Page, Problem } from "../components/Shell";
import { ShareLink } from "../components/ShareLink";
import { BASE_PATH } from "../config";
import { useLang } from "../lang";
import { storedChatToken } from "../useChat";
import { cleanCode, groupLink } from "../visit";
import { useShop } from "./ShopPage";

function useGroup(slug: string, code: string) {
  const [group, setGroup] = useState<GroupView | null>(null);
  const [missing, setMissing] = useState(false);
  const load = useCallback(() => {
    api.group(slug, code, storedChatToken(slug)).then(setGroup, (error: unknown) => {
      if (error instanceof ApiError && error.status === 404) setMissing(true);
    });
  }, [slug, code]);
  useEffect(load, [load]);
  return { group, missing, setGroup };
}

function Part({ part }: { part: GroupPart }) {
  const { t } = useLang();
  return (
    <li>
      <strong dir="auto">{part.name}</strong>
      {part.organiser && <span className="pay-note">{t("group.organiser")}</span>}
      <span className="pay-note">{t("group.items", { count: part.items })}</span>
      <span className={part.paid === false ? "pay-off" : "pay-on"}>
        {part.paid === null
          ? t("group.pays_at_door")
          : part.paid
            ? t("group.paid")
            : t("group.not_paid")}
      </span>
    </li>
  );
}

export function GroupPage() {
  const { slug = "", code: raw = "" } = useParams();
  const code = cleanCode(raw) ?? "";
  const { t, lang } = useLang();
  const navigate = useNavigate();
  const { profile } = useShop(slug);
  const { group, missing, setGroup } = useGroup(slug, code);
  const [closing, setClosing] = useState<"idle" | "closing" | "failed">("idle");

  async function close() {
    const token = storedChatToken(slug);
    if (!token) return;
    setClosing("closing");
    try {
      setGroup(await api.closeGroup(slug, code, token));
      setClosing("idle");
    } catch {
      setClosing("failed");
    }
  }

  if (missing || !code) {
    return (
      <Page>
        <Problem message={t("group.not_found")} />
      </Page>
    );
  }
  if (!group) {
    return (
      <Page>
        <div className="profile-skeleton" aria-busy="true" />
      </Page>
    );
  }
  const closes = new Date(group.closes_at).toLocaleString(lang, {
    weekday: "long",
    day: "numeric",
    month: "long",
    hour: "numeric",
    minute: "2-digit",
  });
  const link = groupLink(window.location.origin, BASE_PATH, slug, group.code);
  return (
    <Page>
      <section className="group-order" aria-labelledby="group-title">
        <h1 id="group-title" className="title" dir="auto">
          {t("group.title", { name: group.organiser })}
        </h1>
        {profile && (
          <p className="lede" dir="auto">
            {profile.name}
          </p>
        )}
        <p className="aside">
          {group.open
            ? t("group.open_until", { city: group.city, when: closes })
            : t("group.closed", { city: group.city })}
        </p>
        <ul className="pay-rows group-parts" aria-label={t("group.parts")}>
          {group.parts.map((part, index) => (
            <Part key={`${part.name}-${index}`} part={part} />
          ))}
        </ul>
        {group.open && (
          <div className="checkout-done-actions">
            <button
              type="button"
              className="button button-saffron"
              onClick={() => navigate(`/s/${slug}?group=${group.code}`)}
            >
              {t("group.add_part")}
            </button>
            <Link to={`/s/${slug}`} className="action">
              {t("checkout.back")}
            </Link>
          </div>
        )}
        {group.open && <p className="pay-note">{t("group.how")}</p>}
        {group.mine && group.open && (
          <div className="pay-block group-lead">
            <h2 className="pay-block-title">{t("group.share_title")}</h2>
            <ShareLink url={link} title={t("group.title", { name: group.organiser })} />
            <button
              type="button"
              className="action"
              disabled={closing === "closing"}
              onClick={() => void close()}
            >
              {t("group.close_now")}
            </button>
            {closing === "failed" && <p role="alert">{t("checkout.err.failed")}</p>}
          </div>
        )}
      </section>
    </Page>
  );
}
