// Making a shop by chatting with Chatfront's own assistant, the same way it is done on
// WhatsApp: a few questions, product photos, a summary, then Confirm.
import { ChatView } from "../components/ChatView";
import { Wordmark } from "../components/Shell";
import type { Key } from "../i18n";
import { useLang } from "../lang";

// Chatfront's own shop, where new shops are made. Its page is hidden; only its chat is open.
const PLATFORM_SLUG = "chatfront";
const STEPS: Key[] = [
  "make.step_name",
  "make.step_type",
  "make.step_city",
  "make.step_delivery",
  "make.step_payment",
  "make.step_products",
];

export function StartPage() {
  const { t } = useLang();
  return (
    <div className="chat-stage start-stage">
      <div className="start-copy">
        <Wordmark />
        <h1 className="start-title">{t("make.title")}</h1>
        <p className="start-lede">{t("make.lede")}</p>
        <ol className="start-steps">
          {STEPS.map((key) => (
            <li key={key}>{t(key)}</li>
          ))}
        </ol>
        <p className="start-note">{t("start.trial_note")}</p>
      </div>
      <ChatView
        slug={PLATFORM_SLUG}
        label={t("make.title")}
        title="Chatfront"
        avatar={{ name: "Chatfront" }}
        back={{ to: "/", label: t("make.back") }}
        note={t("make.note")}
        empty={t("make.empty")}
        suggestions={[t("make.suggest_1")]}
        footer={<p className="phone-credit">{t("app.attribution")}</p>}
      />
    </div>
  );
}
