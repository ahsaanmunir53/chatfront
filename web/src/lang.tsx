import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  dirOf,
  ensureFont,
  fontOf,
  initialLang,
  isLoaded,
  loadCatalog,
  translate,
} from "./i18n";
import type { Key, Lang, Values } from "./i18n";

const STORAGE_KEY = "chatfront.lang";

type LangState = {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: Key, values?: Values) => string;
};

const LangContext = createContext<LangState | null>(null);

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function browserLanguages(): readonly string[] {
  return navigator.languages?.length ? navigator.languages : [navigator.language];
}

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => initialLang(readStored(), browserLanguages()));
  // Bumped when a catalog arrives, so text written in a fallback language is redrawn.
  const [loaded, setLoaded] = useState(() => isLoaded(lang));

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private windows can refuse storage; the choice still holds for this visit.
    }
  }, []);

  useEffect(() => {
    let current = true;
    ensureFont(lang);
    setLoaded(isLoaded(lang));
    loadCatalog(lang).then(
      () => current && setLoaded(true),
      () => current && setLoaded(false), // offline: the fallback language stays on screen
    );
    const root = document.documentElement;
    root.lang = lang;
    root.dir = dirOf(lang);
    root.dataset.font = fontOf(lang).key;
    return () => {
      current = false;
    };
  }, [lang]);

  const value = useMemo<LangState>(
    () => ({ lang, setLang, t: (key, values) => translate(lang, key, values) }),
    // `loaded` is not read here, but a new catalog must give components a new `t`.
    [lang, setLang, loaded],
  );
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): LangState {
  const state = useContext(LangContext);
  if (!state) throw new Error("useLang must be used inside LangProvider");
  return state;
}
