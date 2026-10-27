import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { LANGS } from "../i18n";
import type { Lang } from "../i18n";
import { useLang } from "../lang";

export function LangSwitch({ tone = "light" }: { tone?: "light" | "dark" }) {
  const { lang, setLang, t } = useLang();
  return (
    <div className={`lang-switch lang-switch-${tone}`} role="group" aria-label={t("nav.language")}>
      {LANGS.map((option) => (
        <button
          key={option}
          type="button"
          lang={option}
          aria-pressed={option === lang}
          onClick={() => setLang(option)}
        >
          {t(`lang.${option}` as const)}
        </button>
      ))}
    </div>
  );
}

/** The same choice as LangSwitch, as a compact select for tight headers. */
export function LangSelect() {
  const { lang, setLang, t } = useLang();
  return (
    <select
      className="lang-select"
      aria-label={t("nav.language")}
      value={lang}
      onChange={(e) => setLang(e.target.value as Lang)}
    >
      {LANGS.map((option) => (
        <option key={option} value={option} lang={option}>
          {t(`lang.${option}` as const)}
        </option>
      ))}
    </select>
  );
}

export function Wordmark() {
  return (
    <Link to="/" className="wordmark">
      <span className="wordmark-mark" aria-hidden="true">
        C
      </span>
      Chatfront
    </Link>
  );
}

export function TopBar({ tone = "light" }: { tone?: "light" | "dark" }) {
  return (
    <header className={`topbar topbar-${tone}`}>
      <Wordmark />
      <LangSwitch tone={tone} />
    </header>
  );
}

export function Footer() {
  const { t } = useLang();
  return (
    <footer className="footer">
      <div className="footer-inner">
        <Wordmark />
        <p className="footer-credit">{t("app.attribution")}</p>
      </div>
    </footer>
  );
}

export function Page({ children, tone = "light" }: { children: ReactNode; tone?: "light" | "dark" }) {
  return (
    <div className="page">
      <TopBar tone={tone} />
      <main className="page-main">{children}</main>
      <Footer />
    </div>
  );
}

export function Problem({ message }: { message: string }) {
  return (
    <p className="problem" role="alert">
      {message}
    </p>
  );
}
