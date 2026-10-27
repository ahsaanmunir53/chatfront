import { useEffect, useId, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { languageOf, statusOf, UI_LANGS } from "../i18n";
import { useLang } from "../lang";
import { readOwnerToken } from "../owner";
import { CheckIcon, ChevronIcon, GlobeIcon } from "./Icons";

/** The language picker: every interface language by its own name, beta ones marked. */
export function LangMenu({ tone = "light" }: { tone?: "light" | "dark" }) {
  const { lang, setLang, t } = useLang();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const wrapper = useRef<HTMLDivElement>(null);
  const current = languageOf(lang);

  useEffect(() => {
    if (!open) return;
    wrapper.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
    const close = (event: MouseEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      setOpen(false);
      wrapper.current?.querySelector<HTMLElement>(".lang-trigger")?.focus();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const items = [...(wrapper.current?.querySelectorAll<HTMLElement>("[role=menuitemradio]") ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    items[(at + step + items.length) % items.length]?.focus();
  }

  return (
    <div className={`lang-menu lang-menu-${tone}`} ref={wrapper} onKeyDown={onKeyDown}>
      <button
        type="button"
        className="lang-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`${t("nav.language")}: ${current.native}`}
        onClick={() => setOpen((was) => !was)}
      >
        <GlobeIcon size={18} />
        <span lang={current.tag} dir={current.dir}>
          {current.native}
        </span>
        <ChevronIcon size={16} />
      </button>
      {open && (
        <div className="lang-panel" id={panelId} role="menu" aria-label={t("nav.language")}>
          {UI_LANGS.map((tag) => {
            const option = languageOf(tag);
            return (
              <button
                key={tag}
                type="button"
                role="menuitemradio"
                aria-checked={tag === lang}
                className="lang-option"
                onClick={() => {
                  setLang(tag);
                  setOpen(false);
                }}
              >
                <span className="lang-native" lang={tag} dir={option.dir}>
                  {option.native}
                </span>
                {option.name !== option.native && (
                  <span className="lang-english" lang="en">
                    {option.name}
                  </span>
                )}
                {statusOf(tag) === "beta" && <span className="lang-beta">{t("lang.beta")}</span>}
                {tag === lang && <CheckIcon size={16} className="lang-check" />}
              </button>
            );
          })}
          <p className="lang-note">{t("lang.note")}</p>
        </div>
      )}
    </div>
  );
}

/** The same choice as a compact native select, for tight headers like the chat's. */
export function LangSelect() {
  const { lang, setLang, t } = useLang();
  return (
    <select
      className="lang-select"
      aria-label={t("nav.language")}
      value={lang}
      onChange={(e) => setLang(e.target.value)}
    >
      {UI_LANGS.map((tag) => (
        <option key={tag} value={tag} lang={tag}>
          {languageOf(tag).native}
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
  const { t } = useLang();
  const { pathname } = useLocation();
  const toConsole = readOwnerToken() !== null && pathname !== "/owner";
  return (
    <header className={`topbar topbar-${tone}`}>
      <Wordmark />
      <div className="topbar-end">
        {toConsole && (
          <Link to="/owner" className="topbar-link">
            {t("console.title")}
          </Link>
        )}
        <LangMenu tone={tone} />
      </div>
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
