"use client";

import { motion, AnimatePresence } from "framer-motion";
import { X } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import { useI18n } from "@/lib/i18n";

interface AssetTearSheetProps {
  open: boolean;
  onClose: () => void;
  symbol: string;
  tickerId: string;
  currentPrice: number;
}

function generatePriceHistory(basePrice: number): { day: string; price: number }[] {
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  let price = basePrice * (0.95 + Math.random() * 0.05);
  return days.map((day) => {
    price = price * (0.97 + Math.random() * 0.06);
    return { day, price: Number(price.toFixed(4)) };
  });
}

const MOCK_SUPPLIERS: Record<string, { supplier: string; risk: "Low" | "Medium" | "High" }> = {
  "CAP-100NF": { supplier: "Yageo Corp (Shenzhen)", risk: "Low" },
  "RES-10K": { supplier: "Samsung Electro-Mechanics", risk: "Low" },
  "IC-STM32": { supplier: "STMicroelectronics (HK)", risk: "High" },
  "DIODE-4007": { supplier: "Vishay Intertechnology", risk: "Medium" },
  "LED-5MM": { supplier: "Cree LED (Dongguan)", risk: "Low" },
  "CONN-USBC": { supplier: "Amphenol ICC", risk: "Medium" },
  "XTAL-16M": { supplier: "Epson Toyocom", risk: "Low" },
  "CAP-10UF": { supplier: "Murata Manufacturing", risk: "Low" },
};

const RISK_COLORS: Record<string, string> = {
  Low: "bg-[#00ff88]/20 text-[#00ff88] border-[#00ff88]/30",
  Medium: "bg-[#ffaa00]/20 text-[#ffaa00] border-[#ffaa00]/30",
  High: "bg-[#ff0044]/20 text-[#ff0044] border-[#ff0044]/30",
};

export function AssetTearSheet({ open, onClose, symbol, tickerId, currentPrice }: AssetTearSheetProps) {
  const { t } = useI18n();
  const priceHistory = generatePriceHistory(currentPrice);
  const info = MOCK_SUPPLIERS[symbol] || { supplier: "Unknown Supplier", risk: "Medium" as const };

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/60 z-40"
          />

          <motion.div
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 30, stiffness: 300 }}
            className="fixed right-0 top-0 bottom-0 w-[420px] z-50 glass-dark overflow-y-auto bloomberg-scroll"
          >
            <div className="p-6 flex flex-col h-full">
              <div className="flex items-center justify-between mb-6">
                <div>
                  <p className="text-[9px] font-mono text-gray-500 tracking-widest mb-1">{t("merchant.asset_profile")}</p>
                  <h2 className="text-lg font-mono font-bold text-gray-100">{symbol}</h2>
                  <p className="text-[10px] font-mono text-gray-600 mt-0.5">{tickerId}</p>
                </div>
                <button
                  onClick={onClose}
                  className="p-2 rounded-lg hover:bg-white/10 transition-colors"
                  aria-label="Close asset tear sheet"
                >
                  <X className="w-5 h-5 text-gray-400" />
                </button>
              </div>

              <div className="bg-white/[0.03] rounded-lg p-4 mb-4 border border-white/[0.06]">
                <p className="text-[9px] font-mono text-gray-500 tracking-wider mb-1">{t("merchant.current_price")}</p>
                <span className="text-2xl font-mono font-bold text-gray-100">
                  ${currentPrice.toFixed(4)}
                </span>
              </div>

              <div className="bg-white/[0.03] rounded-lg p-4 mb-4 border border-white/[0.06]">
                <p className="text-[9px] font-mono text-gray-500 tracking-wider mb-3">{t("merchant.price_history")}</p>
                <div className="h-[180px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={priceHistory}>
                      <XAxis
                        dataKey="day"
                        tick={{ fontSize: 9, fontFamily: "monospace", fill: "#666" }}
                        axisLine={{ stroke: "#222" }}
                        tickLine={false}
                      />
                      <YAxis
                        tick={{ fontSize: 9, fontFamily: "monospace", fill: "#666" }}
                        axisLine={false}
                        tickLine={false}
                        width={50}
                        tickFormatter={(v: number) => `$${v.toFixed(3)}`}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "#111",
                          border: "1px solid #333",
                          borderRadius: "6px",
                          fontSize: "10px",
                          fontFamily: "monospace",
                          color: "#00ff88",
                        }}
                        formatter={(value) => [`$${Number(value).toFixed(4)}`, t("common.price")]}
                      />
                      <Line
                        type="monotone"
                        dataKey="price"
                        stroke="#00ff88"
                        strokeWidth={2}
                        dot={{ fill: "#00ff88", r: 3 }}
                        activeDot={{ r: 5, fill: "#00ff88" }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <div className="bg-white/[0.03] rounded-lg p-4 mb-4 border border-white/[0.06]">
                <p className="text-[9px] font-mono text-gray-500 tracking-wider mb-2">{t("merchant.best_supplier")}</p>
                <p className="text-[13px] font-mono text-gray-200">{info.supplier}</p>
              </div>

              <div className="bg-white/[0.03] rounded-lg p-4 border border-white/[0.06]">
                <p className="text-[9px] font-mono text-gray-500 tracking-wider mb-2">{t("merchant.compliance_risk")}</p>
                <span
                  className={`inline-block px-3 py-1 text-[11px] font-mono font-bold rounded border ${RISK_COLORS[info.risk]}`}
                >
                  {info.risk.toUpperCase()}
                </span>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
