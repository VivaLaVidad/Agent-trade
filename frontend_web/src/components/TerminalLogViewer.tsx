"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";

const LOG_LINES = [
  { type: "INFO", text: "Parsing non-standard demand: Mining intrinsically safe 5G base station x 3" },
  { type: "EXEC", text: "Launching local inventory lookup: SKU-5G-CPE-01..." },
  { type: "INFO", text: "Ticker registered: CLAW-ELEC-5GCPE-01 ← telecom / 5G CPE industrial" },
  { type: "WARN", text: "RegGuard interception triggered: unverified SIRIM-certified RF module detected" },
  { type: "EXEC", text: "Initiating alternative supply chain matching..." },
  { type: "INFO", text: "ScatterNode: broadcasting to 6 external supplier nodes via A2A protocol" },
  { type: "SUCCESS", text: "Match found: supplier #3 score 0.94 — Shenzhen YagTech Electronics" },
  { type: "EXEC", text: "NegotiatorAgent: opening round 1 — initial offer $142.50/unit" },
  { type: "INFO", text: "Opponent profiler: client_id=MY-MINING-7821, risk_tag=COOPERATIVE" },
  { type: "SUCCESS", text: "Negotiation complete: $142.50 → $128.00 (-10.2%). Supplier accepted." },
  { type: "EXEC", text: "ArbitrageEvaluator: upstream lock $98.50/unit → spread 23.1%" },
  { type: "SUCCESS", text: "HEDGE LOCKED ✓ — Buy: $98.50 | Sell: $128.00 | Net: $88.50" },
  { type: "CRYPTO", text: "Generating SHA-256 tamper-proof audit hash: a8b3f...9d1e" },
  { type: "INFO", text: "DocuForge: generating Proforma Invoice PI-2026-0421..." },
  { type: "SUCCESS", text: "Transaction settled: TXN-2026-04210892 | $384.00 | fee: $3.84 | sig: verified" },
  { type: "INFO", text: "FX tick: USD/MYR 4.4312 (−0.0018) | shipping: Port Klang → 2.1% landed cost" },
  { type: "EXEC", text: "TickPricingEngine: recalculating spot price — inventory pressure 0.72" },
  { type: "WARN", text: "Volatility spike detected: CLAW-ELEC-5GCPE-01 σ=0.15 — supply shortage trigger" },
  { type: "INFO", text: "Import certification check: Malaysia SIRIM + MCMC — status: CLEARED ✓" },
  { type: "SUCCESS", text: "Pipeline complete — 4 nodes executed in 3.8s, all checks passed" },
];

const TYPE_COLORS: Record<string, string> = {
  INFO: "text-blue-400",
  EXEC: "text-amber-400",
  WARN: "text-yellow-400",
  SUCCESS: "text-emerald-400",
  CRYPTO: "text-purple-400",
};

export function TerminalLogViewer() {
  const [lines, setLines] = useState<{ id: number; type: string; text: string; time: string }[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const idRef = useRef(0);

  useEffect(() => {
    let active = true;
    let idx = 0;

    function addLine() {
      if (!active) return;
      const template = LOG_LINES[idx % LOG_LINES.length];
      const now = new Date();
      const time = now.toLocaleTimeString("en-US", { hour12: false }) + "." + String(now.getMilliseconds()).padStart(3, "0");
      idRef.current++;
      setLines((prev) => [...prev.slice(-30), { id: idRef.current, type: template.type, text: template.text, time }]);
      idx++;
      setTimeout(addLine, 300 + Math.random() * 800);
    }

    setTimeout(addLine, 500);
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines.length]);

  return (
    <div className="bg-black/80 border border-zinc-800 rounded-sm overflow-hidden h-full flex flex-col">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-zinc-800 bg-zinc-950">
        <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse-dot" />
        <span className="text-[9px] font-mono text-zinc-500 tracking-widest uppercase">SYSTEM LOG — LIVE</span>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-2 bloomberg-scroll">
        <AnimatePresence initial={false}>
          {lines.map((line) => (
            <motion.div
              key={line.id}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.15 }}
              className="flex gap-2 py-0.5 text-[10px] font-mono leading-relaxed"
            >
              <span className="text-zinc-600 flex-shrink-0">[{line.time}]</span>
              <span className={`flex-shrink-0 ${TYPE_COLORS[line.type] || "text-zinc-400"}`}>
                {line.type.padEnd(7)}
              </span>
              <span className="text-emerald-400/80">{line.text}</span>
            </motion.div>
          ))}
        </AnimatePresence>
        <span className="text-emerald-400/60 text-[10px] font-mono animate-blink-cursor">█</span>
      </div>
    </div>
  );
}
