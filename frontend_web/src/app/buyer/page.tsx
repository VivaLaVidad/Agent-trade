"use client";

import { useState, useCallback, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import {
  ArrowLeft,
  Search,
  Zap,
  ShieldCheck,
  Globe2,
  Package,
  Clock,
  CheckCircle2,
  Loader2,
  BadgeCheck,
} from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { apiClient } from "@/lib/api/axios-client";

/* ── Mock inventory data (mirrors backend 50 SKUs) ── */
const MOCK_SKUS = [
  { id: "SKU-CAP-001", name: "100nF MLCC 0402", cat: "Capacitor", price: 0.008, margin: 87.5, stock: 50000 },
  { id: "SKU-CAP-002", name: "10uF Tantalum SMD", cat: "Capacitor", price: 0.035, margin: 85.7, stock: 20000 },
  { id: "SKU-IC-001", name: "STM32F103C8T6", cat: "MCU", price: 2.50, margin: 92.0, stock: 5000 },
  { id: "SKU-IC-002", name: "ESP32-WROOM-32E", cat: "MCU", price: 1.80, margin: 94.4, stock: 8000 },
  { id: "SKU-SEN-001", name: "DHT22 Temp/Humidity", cat: "Sensor", price: 1.50, margin: 90.0, stock: 3000 },
  { id: "SKU-SEN-002", name: "MPU6050 6-Axis IMU", cat: "Sensor", price: 0.80, margin: 87.5, stock: 5000 },
  { id: "SKU-IC-007", name: "CH340G USB-UART", cat: "IC", price: 0.35, margin: 85.7, stock: 15000 },
  { id: "SKU-CON-001", name: "USB Type-C 16P SMD", cat: "Connector", price: 0.12, margin: 83.3, stock: 20000 },
  { id: "SKU-RES-001", name: "10K Ohm 0402 1%", cat: "Resistor", price: 0.002, margin: 100.0, stock: 200000 },
  { id: "SKU-DIO-004", name: "LED Red 0805", cat: "LED", price: 0.003, margin: 100.0, stock: 200000 },
  { id: "SKU-TR-003", name: "IRF540N MOSFET TO-220", cat: "Transistor", price: 0.25, margin: 92.0, stock: 10000 },
  { id: "SKU-XTL-002", name: "16MHz Crystal SMD", cat: "Crystal", price: 0.06, margin: 83.3, stock: 20000 },
];

type FlashResult = {
  status: "matched" | "no_match";
  source_type: string;
  sku_match: {
    sku_id: string;
    sku_name: string;
    unit_price_usd: number;
    stock_qty: number;
    profit_margin_pct: number;
    location: string;
  } | null;
  estimated_delivery: string;
  is_un_certified: boolean;
  is_rcep_eligible: boolean;
  recommendation: string;
};

type AIRecommend = {
  ai_recommendation: string;
  risk_notes: string;
  alternative_suggestions: string[];
  status: string;
};

export default function BuyerPage() {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [filtered, setFiltered] = useState(MOCK_SKUS);
  const [phase, setPhase] = useState<"idle" | "searching" | "result" | "ordered">("idle");
  const [result, setResult] = useState<FlashResult | null>(null);
  const [qty, setQty] = useState(1000);
  const [aiRec, setAiRec] = useState<AIRecommend | null>(null);
  const [aiLoading, setAiLoading] = useState(false);

  useEffect(() => {
    if (!query.trim()) {
      setFiltered(MOCK_SKUS);
      return;
    }
    const q = query.toLowerCase();
    setFiltered(
      MOCK_SKUS.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.id.toLowerCase().includes(q) ||
          s.cat.toLowerCase().includes(q)
      )
    );
  }, [query]);

  const handleFlashOrder = useCallback(
    async (skuName: string) => {
      setPhase("searching");

      // Try real API first, fallback to local mock
      try {
        const { data } = await apiClient.post("/api/v1/buyer/flash-intent", {
          sku: skuName,
          quantity: qty,
          target_country: "VN",
          is_urgent: false,
        });
        setResult({
          status: data.status === "matched" ? "matched" : "no_match",
          source_type: data.source_type,
          sku_match: data.sku_match
            ? {
                sku_id: data.sku_match.sku_id,
                sku_name: data.sku_match.sku_name,
                unit_price_usd: data.sku_match.unit_price_usd,
                stock_qty: data.sku_match.stock_qty,
                profit_margin_pct: data.sku_match.profit_margin_pct,
                location: data.sku_match.location,
              }
            : null,
          estimated_delivery: data.estimated_delivery,
          is_un_certified: data.is_un_certified,
          is_rcep_eligible: data.is_rcep_eligible,
          recommendation: data.recommendation,
        });
      } catch {
        // Fallback: local mock matching
        const match = MOCK_SKUS.find(
          (s) => s.name.toLowerCase().includes(skuName.toLowerCase()) || s.id.toLowerCase().includes(skuName.toLowerCase())
        );
        if (match) {
          setResult({
            status: "matched",
            source_type: "LOCAL_INVENTORY",
            sku_match: {
              sku_id: match.id,
              sku_name: match.name,
              unit_price_usd: match.price,
              stock_qty: match.stock,
              profit_margin_pct: match.margin,
              location: "SZ-A1",
            },
            estimated_delivery: match.stock >= qty ? "24 Hours" : "2-3 Business Days",
            is_un_certified: true,
            is_rcep_eligible: true,
            recommendation: `Local match: ${match.name} @ $${match.price}/unit. UN Certified + RCEP 0% Tariff.`,
          });
        } else {
          setResult({
            status: "no_match",
            source_type: "REMOTE_ARBITRAGE",
            sku_match: null,
            estimated_delivery: "3-5 Business Days",
            is_un_certified: false,
            is_rcep_eligible: false,
            recommendation: "No local inventory. Scatter broadcast initiated to external suppliers.",
          });
        }
      }
      setPhase("result");
    },
    [qty]
  );

  // Fetch DeepSeek AI recommendation when result is available
  useEffect(() => {
    if (phase !== "result" || !result?.sku_match) {
      setAiRec(null);
      return;
    }
    let cancelled = false;
    setAiLoading(true);
    apiClient
      .post("/api/v1/buyer/ai-recommend", {
        sku_name: result.sku_match.sku_name,
        quantity: qty,
        target_country: "VN",
        unit_price_usd: result.sku_match.unit_price_usd,
      })
      .then(({ data }) => {
        if (!cancelled) setAiRec(data);
      })
      .catch(() => {
        if (!cancelled) setAiRec(null);
      })
      .finally(() => {
        if (!cancelled) setAiLoading(false);
      });
    return () => { cancelled = true; };
  }, [phase, result, qty]);

  const handleConfirmOrder = useCallback(() => {
    setPhase("ordered");
  }, []);

  return (
    <div className="min-h-screen bg-[#0a0e14] text-white">
      {/* ── Header ── */}
      <header className="sticky top-0 z-50 bg-[#0a0e14]/95 backdrop-blur-md border-b border-white/5">
        <div className="flex items-center justify-between px-4 sm:px-6 py-3">
          <div className="flex items-center gap-3">
            <Link href="/" className="p-1.5 rounded-lg hover:bg-white/5 transition-colors" aria-label="Back">
              <ArrowLeft className="w-4 h-4 text-gray-500" />
            </Link>
            <div>
              <h1 className="text-sm font-light tracking-tight">
                Omni<span className="font-bold text-emerald-400">Edge</span>
              </h1>
              <p className="text-[10px] text-gray-500 font-mono">{t("buyer.title")}</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden sm:inline text-[10px] text-gray-600 font-mono">Huaqiangbei Direct</span>
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <LanguageSwitcher />
          </div>
        </div>
      </header>

      {/* ── Main ── */}
      <main className="max-w-lg mx-auto px-4 sm:px-6 pt-6 pb-24">
        {/* Trust Banner */}
        <div className="flex items-center gap-2 mb-5 overflow-x-auto scrollbar-hide">
          <div className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-blue-500/10 border border-blue-500/20">
            <ShieldCheck className="w-3.5 h-3.5 text-blue-400" />
            <span className="text-[11px] text-blue-300 whitespace-nowrap">UN Certified</span>
          </div>
          <div className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20">
            <Globe2 className="w-3.5 h-3.5 text-emerald-400" />
            <span className="text-[11px] text-emerald-300 whitespace-nowrap">RCEP 0% Tariff</span>
          </div>
          <div className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-amber-500/10 border border-amber-500/20">
            <Zap className="w-3.5 h-3.5 text-amber-400" />
            <span className="text-[11px] text-amber-300 whitespace-nowrap">24h Express</span>
          </div>
        </div>

        {/* Speed Sourcing Bar */}
        <div className="relative mb-5">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("buyer.placeholder")}
            className="w-full pl-10 pr-4 py-3 bg-white/5 border border-white/10 rounded-xl text-sm text-white placeholder-gray-500 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/20 transition-all"
          />
        </div>

        {/* Quantity selector */}
        <div className="flex items-center gap-3 mb-5">
          <label className="text-xs text-gray-500">{t("buyer.qty")}:</label>
          <div className="flex gap-1.5">
            {[100, 500, 1000, 5000].map((q) => (
              <button
                key={q}
                onClick={() => setQty(q)}
                className={`px-3 py-1 text-xs rounded-lg transition-all ${
                  qty === q
                    ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
                    : "bg-white/5 text-gray-400 border border-white/5 hover:border-white/10"
                }`}
              >
                {q >= 1000 ? `${q / 1000}K` : q}
              </button>
            ))}
          </div>
        </div>

        {/* SKU Grid */}
        <AnimatePresence mode="wait">
          {phase === "idle" && (
            <motion.div
              key="grid"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="grid grid-cols-1 sm:grid-cols-2 gap-3"
            >
              {filtered.map((sku) => (
                <motion.div
                  key={sku.id}
                  layout
                  className="bg-white/[0.03] border border-white/5 rounded-xl p-4 hover:border-emerald-500/20 transition-all group"
                >
                  <div className="flex items-start justify-between mb-2">
                    <div>
                      <p className="text-sm font-medium text-white/90 leading-tight">{sku.name}</p>
                      <p className="text-[10px] text-gray-500 font-mono mt-0.5">{sku.id}</p>
                    </div>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-gray-400">{sku.cat}</span>
                  </div>

                  <div className="flex items-center gap-3 mb-3">
                    <span className="text-lg font-bold text-emerald-400">${sku.price}</span>
                    <span className="text-[10px] text-gray-500">/unit</span>
                    <span className="ml-auto text-[10px] text-gray-500">
                      <Package className="w-3 h-3 inline mr-0.5" />
                      {sku.stock >= 1000 ? `${(sku.stock / 1000).toFixed(0)}K` : sku.stock}
                    </span>
                  </div>

                  {/* Trust Badges */}
                  <div className="flex items-center gap-1.5 mb-3">
                    <span className="flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full bg-blue-500/10 text-blue-300">
                      <BadgeCheck className="w-2.5 h-2.5" /> UN
                    </span>
                    <span className="flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-300">
                      <Globe2 className="w-2.5 h-2.5" /> RCEP
                    </span>
                  </div>

                  {/* Flash Order Button */}
                  <button
                    onClick={() => handleFlashOrder(sku.name)}
                    className="w-full py-2 text-xs font-medium rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 hover:bg-emerald-500/20 active:scale-[0.98] transition-all flex items-center justify-center gap-1.5"
                  >
                    <Zap className="w-3.5 h-3.5" />
                    {t("buyer.order")}
                  </button>
                </motion.div>
              ))}

              {filtered.length === 0 && (
                <div className="col-span-full text-center py-12 text-gray-500 text-sm">
                  No matching SKUs. Try &quot;capacitor&quot;, &quot;STM32&quot;, or &quot;sensor&quot;.
                </div>
              )}
            </motion.div>
          )}

          {/* Searching */}
          {phase === "searching" && (
            <motion.div
              key="searching"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex flex-col items-center py-16"
            >
              <Loader2 className="w-10 h-10 text-emerald-400 animate-spin mb-4" />
              <p className="text-sm text-gray-400">{t("buyer.searching")}</p>
              <p className="text-[10px] text-gray-600 mt-1 font-mono">local_inventory_node → reg_guard → flash_intent</p>
            </motion.div>
          )}

          {/* Result */}
          {phase === "result" && result && (
            <motion.div
              key="result"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
            >
              <div className="bg-white/[0.03] border border-white/10 rounded-xl p-5">
                {/* Status badge */}
                <div className="flex items-center gap-2 mb-4">
                  <span
                    className={`px-2.5 py-1 text-[11px] font-medium rounded-full ${
                      result.source_type === "LOCAL_INVENTORY"
                        ? "bg-emerald-500/15 text-emerald-400 border border-emerald-500/20"
                        : "bg-amber-500/15 text-amber-400 border border-amber-500/20"
                    }`}
                  >
                    {result.source_type === "LOCAL_INVENTORY" ? "✓ Local Match" : "⟳ External Sourcing"}
                  </span>
                </div>

                {result.sku_match && (
                  <div className="space-y-3 mb-4">
                    <div className="flex justify-between items-baseline">
                      <p className="text-base font-medium">{result.sku_match.sku_name}</p>
                      <span className="text-xs text-gray-500 font-mono">{result.sku_match.sku_id}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="bg-white/5 rounded-lg p-3">
                        <p className="text-[10px] text-gray-500 mb-1">{t("common.price")}</p>
                        <p className="text-lg font-bold text-emerald-400">${result.sku_match.unit_price_usd}</p>
                      </div>
                      <div className="bg-white/5 rounded-lg p-3">
                        <p className="text-[10px] text-gray-500 mb-1">{t("common.quantity")}</p>
                        <p className="text-lg font-bold text-white">
                          {result.sku_match.stock_qty >= 1000
                            ? `${(result.sku_match.stock_qty / 1000).toFixed(0)}K`
                            : result.sku_match.stock_qty}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {/* Delivery + Compliance */}
                <div className="space-y-2 mb-4">
                  <div className="flex items-center gap-2">
                    <Clock className="w-3.5 h-3.5 text-gray-500" />
                    <span className="text-xs text-gray-300">{result.estimated_delivery}</span>
                  </div>
                  {result.is_un_certified && (
                    <div className="flex items-center gap-2">
                      <ShieldCheck className="w-3.5 h-3.5 text-blue-400" />
                      <span className="text-xs text-blue-300">UN Certified — No factory audit required</span>
                    </div>
                  )}
                  {result.is_rcep_eligible && (
                    <div className="flex items-center gap-2">
                      <Globe2 className="w-3.5 h-3.5 text-emerald-400" />
                      <span className="text-xs text-emerald-300">RCEP Eligible — 0% import tariff (VN/TH/SG/MY)</span>
                    </div>
                  )}
                </div>

                <p className="text-xs text-gray-400 mb-4">{result.recommendation}</p>

                {/* DeepSeek AI Recommendation Panel */}
                {result.sku_match && (
                  <div className="mb-4 p-3 rounded-lg bg-purple-500/5 border border-purple-500/20">
                    <div className="flex items-center gap-2 mb-2">
                      <div className="w-4 h-4 rounded bg-gradient-to-br from-purple-400 to-pink-500 flex items-center justify-center">
                        <span className="text-[8px] text-white font-bold">AI</span>
                      </div>
                      <span className="text-[10px] text-purple-300 font-mono uppercase tracking-wider">
                        DeepSeek Procurement Advisor
                      </span>
                      {aiLoading && <Loader2 className="w-3 h-3 text-purple-400 animate-spin ml-auto" />}
                    </div>
                    {aiLoading && !aiRec && (
                      <p className="text-[11px] text-gray-500 italic">Analyzing procurement parameters...</p>
                    )}
                    {aiRec && aiRec.status === "ok" && (
                      <div className="space-y-2">
                        <p className="text-xs text-gray-300 leading-relaxed">{aiRec.ai_recommendation}</p>
                        {aiRec.risk_notes && (
                          <p className="text-[11px] text-amber-400/80 leading-relaxed">⚠ {aiRec.risk_notes}</p>
                        )}
                        {aiRec.alternative_suggestions.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1">
                            {aiRec.alternative_suggestions.map((alt, i) => (
                              <span
                                key={i}
                                className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 text-gray-400 border border-white/5"
                              >
                                {alt}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                    {aiRec && aiRec.status !== "ok" && (
                      <p className="text-[11px] text-gray-500">{aiRec.ai_recommendation}</p>
                    )}
                  </div>
                )}

                {result.status === "matched" && (
                  <button
                    onClick={handleConfirmOrder}
                    className="w-full py-3 text-sm font-semibold rounded-xl bg-emerald-500 text-white hover:bg-emerald-600 active:scale-[0.98] transition-all flex items-center justify-center gap-2"
                  >
                    <Zap className="w-4 h-4" />
                    {t("buyer.order")} — {qty} units
                  </button>
                )}

                <button
                  onClick={() => {
                    setPhase("idle");
                    setResult(null);
                  }}
                  className="w-full mt-2 py-2 text-xs text-gray-500 hover:text-gray-300 transition-colors"
                >
                  {t("buyer.back")}
                </button>
              </div>
            </motion.div>
          )}

          {/* Ordered */}
          {phase === "ordered" && (
            <motion.div
              key="ordered"
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="flex flex-col items-center py-16"
            >
              <motion.div
                initial={{ scale: 0 }}
                animate={{ scale: 1 }}
                transition={{ type: "spring", damping: 10 }}
                className="w-16 h-16 rounded-full bg-emerald-500/20 flex items-center justify-center mb-4"
              >
                <CheckCircle2 className="w-8 h-8 text-emerald-400" />
              </motion.div>
              <h3 className="text-lg font-semibold mb-1">Order Confirmed</h3>
              <p className="text-xs text-gray-500 mb-1">
                {result?.sku_match?.sku_name} × {qty} units
              </p>
              <p className="text-[10px] text-gray-600 font-mono mb-6">
                Estimated: {result?.estimated_delivery}
              </p>
              <button
                onClick={() => {
                  setPhase("idle");
                  setResult(null);
                  setQuery("");
                }}
                className="px-6 py-2 text-xs text-emerald-400 hover:bg-emerald-500/10 rounded-lg transition-colors"
              >
                {t("buyer.search")}
              </button>
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      {/* Bottom safe area for mobile */}
      <div className="h-6" />
    </div>
  );
}
