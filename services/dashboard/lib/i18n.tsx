"use client";
// UI localization. English source strings are the keys; lib/i18n-zh.ts maps them to Simplified Chinese.
// A string missing from the dictionary falls back to English, so untranslated text never breaks the UI.
// The chosen language lives in the `lang` cookie so the server renders the same language as the client.
import { createContext, Fragment, isValidElement, useCallback, useContext, useState, type ReactNode } from "react";
import { zh } from "./i18n-zh";
import { LANG_COOKIE, LANGS, type Lang } from "./lang";

type Vars = Record<string, ReactNode>;
type PlainVars = Record<string, string | number | null | undefined>;

export interface T {
  /** Translate `s`, filling `{name}` placeholders from `vars`. */
  (s: string, vars?: PlainVars): string;
  /** Same, but `vars` may hold elements (links, code); returns a fragment. */
  (s: string, vars: Vars): ReactNode;
}

function translate(lang: Lang, s: string, vars?: Vars): ReactNode {
  const src = (lang === "zh" && zh[s]) || s;
  if (!vars) return src;
  const rich = Object.values(vars).some((v) => isValidElement(v));
  const parts = src.split(/\{(\w+)\}/g);
  if (!rich) return parts.map((p, i) => (i % 2 ? String(vars[p] ?? "") : p)).join("");
  return parts.map((p, i) => <Fragment key={i}>{i % 2 ? vars[p] : p}</Fragment>);
}

interface I18nCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: T;
}

const Ctx = createContext<I18nCtx>({ lang: "en", setLang: () => {}, t: ((s: string, v?: Vars) => translate("en", s, v)) as T });

export function I18nProvider({ initial, children }: { initial: Lang; children: ReactNode }) {
  const [lang, setState] = useState<Lang>(initial);
  const setLang = useCallback((l: Lang) => {
    document.cookie = `${LANG_COOKIE}=${l}; path=/; max-age=31536000; samesite=lax`;
    document.documentElement.lang = l === "zh" ? "zh-CN" : "en";
    setState(l);
  }, []);
  const t = useCallback(((s: string, v?: Vars) => translate(lang, s, v)) as T, [lang]);
  return <Ctx.Provider value={{ lang, setLang, t }}>{children}</Ctx.Provider>;
}

export const useI18n = () => useContext(Ctx);
export const useT = () => useContext(Ctx).t;

/** EN / 中文 switch. */
export function LangToggle() {
  const { lang, setLang } = useI18n();
  return (
    <span className="lang-toggle" role="group" aria-label="Language / 语言">
      {LANGS.map((l) => (
        <button key={l} type="button" className={lang === l ? "on" : ""} aria-pressed={lang === l} onClick={() => setLang(l)}>
          {l === "en" ? "EN" : "中文"}
        </button>
      ))}
    </span>
  );
}
