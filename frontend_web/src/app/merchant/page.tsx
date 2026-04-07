"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { ConnectionStatus } from "@/components/merchant/ConnectionStatus";
import { MarketTickFeed } from "@/components/merchant/MarketTickFeed";
import { HITLOverridePanel } from "@/components/merchant/HITLOverridePanel";
import { EventFeed } from "@/components/merchant/EventFeed";
import { CommandBar } from "@/components/merchant/CommandBar";

export default function MerchantPage() {
  const [utcTime, setUtcTime] = useState("");
  const [pnl, setPnl] = useState({ value: 0, positive: true });

  // UTC clock
  useEffect(() => {
    const tick = () => {
      setUtcTime(
        new Date().toLocaleTimeString("en-US", {
          hour12: false,
          timeZone: "UTC",
        }) + " UTC",
      );
    };
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, []);

  // Simulated P&L
  useEffect(() => {
    const interval = setInterval(() => {
      setPnl((prev) => {
        const delta = (Math.random() - 0.45) * 15;
        const next = prev.value + delta;
        return { value: next, positive: next >= 0 };
      });
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  // Ref to HITL panel for command bar integration
  const hitlRef = useRef<{ accept: (id: string) => void; reject: (id: string) => void } | null>(null);

  const handleOverride = useCallback((tradeId: string, _margin: number) => {
    hitlRef.current?.accept(tradeId);
  }, []);

  const handleKill = useCallback((tradeId: string) => {
    hitlRef.current?.reject(tradeId);
  }, []);

  return (
    <div className="h-screen flex flex-col bg-[#0a0a0a] text-gray-200 font-mono overflow-hidden">
      {/* Top status bar */}
      <header className="flex items-center justify-between px-4 py-2 border-b border-[#1a1a1a] flex-shrink-0">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="p-1.5 rounded hover:bg-[#1a1a1a] transition-colors"
            aria-label="Back to home"
          >
            <ArrowLeft className="w-4 h-4 text-gray-500" />
          </Link>
          <span className="text-[11px] font-bold text-gray-300 tracking-widest">
            ARBITRAGE DESK v2.0
          </span>
          <ConnectionStatus connected={true} />
        </div>
        <div className="flex items-center gap-6">
          {/* P&L */}
          <div className="flex items-center gap-2">
            <span className="text-[9px] text-gray-600 tracking-wider">TODAY P&L</span>
            <span
              className={`text-sm font-bold ${
                pnl.positive ? "text-[#00ff88]" : "text-[#ff0044]"
              }`}
            >
              {pnl.positive ? "+" : ""}${pnl.value.toFixed(2)}
            </span>
          </div>
          {/* UTC Clock */}
          <span className="text-[11px] text-gray-500">{utcTime}</span>
        </div>
      </header>

      {/* Main 3-column layout — leave room for command bar at bottom */}
      <main className="flex-1 grid grid-cols-[300px_1fr_350px] min-h-0 pb-10">
        {/* Left: Market Tick Feed */}
        <div className="border-r border-[#1a1a1a] min-h-0 overflow-hidden">
          <MarketTickFeed />
        </div>

        {/* Center: HITL Override */}
        <div className="border-r border-[#1a1a1a] min-h-0 overflow-hidden">
          <HITLOverridePanel ref={hitlRef} />
        </div>

        {/* Right: Event Feed */}
        <div className="min-h-0 overflow-hidden">
          <EventFeed />
        </div>
      </main>

      {/* Command Bar */}
      <CommandBar onOverride={handleOverride} onKill={handleKill} />
    </div>
  );
}
