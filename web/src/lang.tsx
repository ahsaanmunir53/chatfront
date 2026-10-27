import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { dirOf, initialLang, translate } from "./i18n";
import type { Key, Lang } from "./i18n";

const STORAGE_KEY = "chatfront.lang";

type LangState = {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: Key, values?: Record<string, string | number>) => string;
};

const LangContext = createContext<LangState | null>(null);

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => initialLang(readStored(), navigator.language));

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private windows can refuse storage; the choice still holds for this visit.
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = dirOf(lang);
  }, [lang]);

  const value = useMemo<LangState>(
    () => ({ lang, setLang, t: (key, values) => translate(lang, key, values) }),
    [lang, setLang],
  );
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang(): LangState {
  const state = useContext(LangContext);
  if (!state) throw new Error("useLang must be used inside LangProvider");
  return state;
}
