"use client";

import { createContext, useContext, useState, useCallback, type ReactNode } from "react";

// ── Supported Languages ──
export type Locale = "en" | "zh" | "ms";

export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English",
  zh: "中文",
  ms: "Bahasa Melayu",
};

// ── Translation Dictionary ──
const translations: Record<string, Record<Locale, string>> = {
  // ─ Brand ─
  "brand.name": { en: "TradeForge", zh: "TradeForge", ms: "TradeForge" },
  "brand.tagline": {
    en: "AI-powered cross-border industrial trade platform",
    zh: "AI 驱动的跨境工业品自动化撮合平台",
    ms: "Platform perdagangan industri rentas sempadan berkuasa AI",
  },

  // ─ Home Page ─
  "home.buyer.title": { en: "Buyer Portal", zh: "采购入口", ms: "Portal Pembeli" },
  "home.buyer.desc": {
    en: "Describe what you need, AI handles sourcing & compliance",
    zh: "描述您的需求，AI 自动寻源并完成合规审查",
    ms: "Nyatakan keperluan anda, AI uruskan sumber & pematuhan",
  },
  "home.merchant.title": { en: "Trade Desk", zh: "交易台", ms: "Meja Dagangan" },
  "home.merchant.desc": {
    en: "Real-time order monitoring & approval dashboard",
    zh: "实时订单监控与审批面板",
    ms: "Papan pemuka pemantauan & kelulusan pesanan masa nyata",
  },
  "home.admin.title": { en: "Command Center", zh: "指挥中心", ms: "Pusat Arahan" },
  "home.admin.desc": {
    en: "Global trade routes overview & audit stream",
    zh: "全球贸易路线概览与审计日志",
    ms: "Gambaran keseluruhan laluan dagangan global & aliran audit",
  },
  "home.enter": { en: "Enter →", zh: "进入 →", ms: "Masuk →" },

  // ─ Buyer Page ─
  "buyer.title": { en: "Buyer Portal", zh: "采购入口", ms: "Portal Pembeli" },
  "buyer.placeholder": {
    en: "Describe what you need...",
    zh: "描述您需要采购的商品...",
    ms: "Nyatakan apa yang anda perlukan...",
  },
  "buyer.search": { en: "Search", zh: "搜索", ms: "Cari" },
  "buyer.searching": { en: "Searching...", zh: "正在搜索...", ms: "Mencari..." },
  "buyer.results": { en: "Results", zh: "搜索结果", ms: "Keputusan" },
  "buyer.order": { en: "Place Order", zh: "下单", ms: "Buat Pesanan" },
  "buyer.back": { en: "← Back", zh: "← 返回", ms: "← Kembali" },
  "buyer.qty": { en: "Quantity", zh: "数量", ms: "Kuantiti" },

  // ─ Merchant Page ─
  "merchant.title": { en: "TRADE DESK", zh: "交易台", ms: "MEJA DAGANGAN" },
  "merchant.live": { en: "LIVE", zh: "实时", ms: "LANGSUNG" },
  "merchant.disconnected": { en: "DISCONNECTED", zh: "已断开", ms: "TERPUTUS" },
  "merchant.pnl": { en: "TODAY P&L", zh: "今日盈亏", ms: "P&L HARI INI" },
  "merchant.market_data": { en: "MARKET DATA", zh: "行情数据", ms: "DATA PASARAN" },
  "merchant.hitl": { en: "APPROVAL QUEUE", zh: "待审批队列", ms: "BARISAN KELULUSAN" },
  "merchant.events": { en: "EVENT STREAM", zh: "事件流", ms: "ALIRAN PERISTIWA" },
  "merchant.no_pending": { en: "NO PENDING REVIEWS", zh: "暂无待审批订单", ms: "TIADA SEMAKAN MENUNGGU" },
  "merchant.accept": { en: "APPROVE", zh: "批准", ms: "LULUS" },
  "merchant.reject": { en: "REJECT", zh: "拒绝", ms: "TOLAK" },
  "merchant.cmd_placeholder": { en: "Type command...", zh: "输入命令...", ms: "Taip arahan..." },

  // ─ Admin Page ─
  "admin.title": { en: "Command Center", zh: "指挥中心", ms: "Pusat Arahan" },

  // ─ Common ─
  "common.loading": { en: "Loading...", zh: "加载中...", ms: "Memuatkan..." },
  "common.error": { en: "Error", zh: "错误", ms: "Ralat" },
  "common.price": { en: "Price", zh: "价格", ms: "Harga" },
  "common.product": { en: "Product", zh: "产品", ms: "Produk" },
  "common.country": { en: "Country", zh: "国家", ms: "Negara" },
  "common.quantity": { en: "Quantity", zh: "数量", ms: "Kuantiti" },
  "common.total": { en: "Total", zh: "合计", ms: "Jumlah" },
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
      const saved = localStorage.getItem("tradeforge_locale") as Locale;
      if (saved && saved in LOCALE_LABELS) return saved;
    }
    return "en";
  });

  const setLocale = useCallback((newLocale: Locale) => {
    setLocaleState(newLocale);
    if (typeof window !== "undefined") {
      localStorage.setItem("tradeforge_locale", newLocale);
    }
  }, []);

  const t = useCallback(
    (key: string): string => {
      const entry = translations[key];
      if (!entry) return key;
      return entry[locale] ?? entry.en ?? key;
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
