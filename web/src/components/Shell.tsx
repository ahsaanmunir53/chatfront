import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { LANGS } from "../i18n";
import { useLang } from "../lang";

export function LangSwitch() {
  const { lang, setLang, t } = useLang();
  return (
    <div className="lang-switch" role="group" aria-label={t("nav.language")}>
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

export function Shell({ children }: { children: ReactNode }) {
  const { t } = useLang();
  return (
    <div className="page">
      <header className="topbar">
        <Link to="/" className="brand">
          {t("app.name")}
        </Link>
        <LangSwitch />
      </header>
      <main className="content">{children}</main>
      <footer className="footer">
        <p>{t("app.attribution")}</p>
        <p>{t("app.powered_by")}</p>
      </footer>
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
