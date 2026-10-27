import { useParams, useSearchParams } from "react-router-dom";
import { ChatView } from "../components/ChatView";
import { useLang } from "../lang";
import { useShop } from "./ShopPage";

const SUGGESTIONS = ["chat.suggest_1", "chat.suggest_2", "chat.suggest_3", "chat.suggest_4"] as const;

/** The try-it chat: the shop's customer assistant, in a phone. */
export function ChatPage() {
  const { slug = "" } = useParams();
  const [search] = useSearchParams();
  const { t } = useLang();
  const { profile } = useShop(slug);
  const name = profile?.name ?? "";
  return (
    <div className="chat-stage">
      <ChatView
        slug={slug}
        label={t("chat.title", { shop: name })}
        title={name}
        avatar={{ name, template: profile?.template }}
        back={{ to: `/s/${slug}`, label: t("chat.back") }}
        note={t("chat.demo_note")}
        empty={t("chat.empty")}
        suggestions={SUGGESTIONS.map((key) => t(key))}
        initialDraft={search.get("ask") ?? ""}
        footer={<p className="phone-credit">{t("app.attribution")}</p>}
      />
    </div>
  );
}
