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
  "merchant.hitl": { en: "HITL OVERRIDE", zh: "人工审批", ms: "SEMAKAN MANUAL" },
  "merchant.events": { en: "EVENT STREAM", zh: "事件流", ms: "ALIRAN PERISTIWA" },
  "merchant.no_pending": { en: "NO PENDING REVIEWS", zh: "暂无待审批订单", ms: "TIADA SEMAKAN MENUNGGU" },
  "merchant.accept": { en: "ACCEPT (OVRD)", zh: "批准 (OVRD)", ms: "LULUS (OVRD)" },
  "merchant.reject": { en: "REJECT (KILL)", zh: "拒绝 (KILL)", ms: "TOLAK (KILL)" },
  "merchant.cmd_placeholder": { en: "OVRD TRD-7821 4.5  |  KILL TRD-7821", zh: "OVRD TRD-7821 4.5  |  KILL TRD-7821", ms: "OVRD TRD-7821 4.5  |  KILL TRD-7821" },
  "merchant.pending": { en: "PENDING", zh: "待审批", ms: "MENUNGGU" },
  "merchant.confirmed": { en: "CONFIRMED ✓", zh: "已批准 ✓", ms: "DISAHKAN ✓" },
  "merchant.killed": { en: "KILLED ✗", zh: "已拒绝 ✗", ms: "DITOLAK ✗" },
  "merchant.profit_margin": { en: "PROFIT MARGIN (GREY ZONE)", zh: "利润率 (灰色地带)", ms: "MARGIN KEUNTUNGAN (ZON KELABU)" },
  "merchant.msgs": { en: "msgs", zh: "条消息", ms: "mesej" },
  "merchant.ticks": { en: "ticks", zh: "条行情", ms: "tik" },
  "merchant.cmds": { en: "cmds", zh: "条命令", ms: "arahan" },
  "merchant.symbol": { en: "Symbol", zh: "代码", ms: "Simbol" },
  "merchant.chg": { en: "Chg%", zh: "涨跌%", ms: "Ubah%" },
  "merchant.vol": { en: "Vol", zh: "成交量", ms: "Vol" },
  "merchant.asset_profile": { en: "ASSET PROFILE", zh: "资产概况", ms: "PROFIL ASET" },
  "merchant.current_price": { en: "CURRENT PRICE", zh: "当前价格", ms: "HARGA SEMASA" },
  "merchant.price_history": { en: "7-DAY PRICE HISTORY", zh: "7日价格走势", ms: "SEJARAH HARGA 7 HARI" },
  "merchant.best_supplier": { en: "BEST UPSTREAM SUPPLIER", zh: "最优上游供应商", ms: "PEMBEKAL HULUAN TERBAIK" },
  "merchant.compliance_risk": { en: "REGGUARD COMPLIANCE RISK", zh: "合规风险等级", ms: "RISIKO PEMATUHAN REGGUARD" },
  "merchant.buyer": { en: "Buyer", zh: "买方", ms: "Pembeli" },
  "merchant.risk_score": { en: "Risk Score", zh: "风险评分", ms: "Skor Risiko" },

  // ─ Admin Page ─
  "admin.title": { en: "Command Center", zh: "指挥中心", ms: "Pusat Arahan" },
  "admin.inquiries": { en: "Today's Inquiries", zh: "今日询价", ms: "Pertanyaan Hari Ini" },
  "admin.hedge_success": { en: "Hedge Success", zh: "对冲成功", ms: "Kejayaan Lindung Nilai" },
  "admin.regguard_blocks": { en: "RegGuard Blocks", zh: "合规拦截", ms: "Sekatan RegGuard" },
  "admin.authenticated": { en: "AUTHENTICATED", zh: "已认证", ms: "DISAHKAN" },
  "admin.locked": { en: "LOCKED", zh: "已锁定", ms: "DIKUNCI" },
  "admin.token_title": { en: "Admin Access", zh: "管理员访问", ms: "Akses Pentadbir" },
  "admin.token_desc": { en: "Enter your hardware token to access the dashboard.", zh: "输入您的硬件令牌以访问仪表板。", ms: "Masukkan token perkakasan anda untuk mengakses papan pemuka." },
  "admin.authenticate": { en: "Authenticate", zh: "认证", ms: "Sahkan" },
  "admin.token_hint": { en: "Demo: enter any 4+ character token", zh: "演示：输入任意4位以上字符", ms: "Demo: masukkan sebarang token 4+ aksara" },

  // ─ Common ─
  "common.loading": { en: "Loading...", zh: "加载中...", ms: "Memuatkan..." },
  "common.error": { en: "Error", zh: "错误", ms: "Ralat" },
  "common.price": { en: "Price", zh: "价格", ms: "Harga" },
  "common.product": { en: "Product", zh: "产品", ms: "Produk" },
  "common.country": { en: "Country", zh: "国家", ms: "Negara" },
  "common.quantity": { en: "Quantity", zh: "数量", ms: "Kuantiti" },
  "common.total": { en: "Total", zh: "合计", ms: "Jumlah" },
  "common.qty": { en: "Qty", zh: "数量", ms: "Kuantiti" },
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
