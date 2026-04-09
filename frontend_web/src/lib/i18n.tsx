"use client";

import { createContext, useContext, useState, useCallback, type ReactNode } from "react";

import en from "./locales/en.json";
import zh from "./locales/zh.json";
import ms from "./locales/ms.json";
import vi from "./locales/vi.json";
import th from "./locales/th.json";
import id from "./locales/id.json";
import tl from "./locales/tl.json";
import lo from "./locales/lo.json";
import km from "./locales/km.json";
import my from "./locales/my.json";
import ptTL from "./locales/pt-TL.json";

// ── Supported Languages (ASEAN + English + Chinese) ──
export type Locale = "en" | "zh" | "ms" | "vi" | "th" | "id" | "tl" | "lo" | "km" | "my" | "pt-TL";

export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English",
  zh: "中文",
  vi: "Tiếng Việt",
  th: "ไทย",
  lo: "ລາວ",
  km: "ខ្មែរ",
  my: "မြန်မာ",
  ms: "Bahasa Melayu",
  id: "Bahasa Indonesia",
  tl: "Filipino",
  "pt-TL": "Português (Timor-Leste)",
};

// ── Flat locale bundles ──
const bundles: Record<Locale, Record<string, string>> = {
  en, zh, ms, vi, th, id, tl, lo, km, my, "pt-TL": ptTL,
};

// ── Context ──
interface I18nContextType {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string) => string;
}

const I18nContext = createContext<I18nContextType>({
  locale: "en",
  setLocale: () => {},
  t: (key) => key,
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("omniedge_locale") as Locale;
      if (saved && saved in LOCALE_LABELS) return saved;
    }
    return "en";
  });

  const setLocale = useCallback((newLocale: Locale) => {
    setLocaleState(newLocale);
    if (typeof window !== "undefined") {
      localStorage.setItem("omniedge_locale", newLocale);
    }
  }, []);

  const t = useCallback(
    (key: string): string => {
      const bundle = bundles[locale];
      if (bundle && key in bundle) return bundle[key];
      const fallback = bundles.en;
      if (fallback && key in fallback) return fallback[key];
      return key;
    },
    [locale],
  );

  return (
    <I18nContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}
