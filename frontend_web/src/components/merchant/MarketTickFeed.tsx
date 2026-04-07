"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import type { MarketTick } from "@/lib/api/types";
import { startMarketTickStream } from "@/lib/api/mock-data";
import { AssetTearSheet } from "./AssetTearSheet";

const MAX_TICKS = 100;

export function MarketTickFeed() {
  const [ticks, setTicks] = useState<(MarketTick & { flash?: string })[]>([]);
  const [selectedTick, setSelectedTick] = useState<MarketTick | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const addTick = useCallback((tick: MarketTick) => {
    setTicks((prev) => {
      const flash = tick.change_pct >= 0 ? "flash-green" : "flash-red";
      const next = [{ ...tick, flash }, ...prev];
      return next.slice(0, MAX_TICKS);
    });
  }, []);

  useEffect(() => {
    const stop = startMarketTickStream(addTick);
    return stop;
  }, [addTick]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-[#1a1a1a]">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-mono font-semibold text-gray-300 tracking-wider">
            MARKET DATA
          </span>
          <div className="w-1.5 h-1.5 rounded-full bg-[#00ff88] animate-pulse-dot" />
        </div>
        <span className="text-[10px] font-mono text-gray-600">{ticks.length} ticks</span>
      </div>

      {/* Column headers */}
      <div className="grid grid-cols-4 gap-1 px-3 py-1.5 text-[9px] font-mono text-gray-600 uppercase tracking-wider border-b border-[#1a1a1a]">
        <span>Symbol</span>
        <span className="text-right">Price</span>
        <span className="text-right">Chg%</span>
        <span className="text-right">Vol</span>
      </div>

      {/* Tick rows */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto bloomberg-scroll">
        {ticks.map((tick, i) => (
          <div
            key={`${tick.ticker_id}-${tick.timestamp}-${i}`}
            onClick={() => setSelectedTick(tick)}
            className={`grid grid-cols-4 gap-1 px-3 py-1 text-[11px] font-mono border-b border-[#111] hover:bg-[#1a1a1a] cursor-pointer transition-colors ${tick.flash ?? ""}`}
          >
            <span className="text-gray-300 truncate">{tick.symbol}</span>
            <span className="text-right text-gray-200">${tick.price.toFixed(4)}</span>
            <span
              className={`text-right font-semibold ${
                tick.change_pct >= 0 ? "text-[#00ff88]" : "text-[#ff0044]"
              }`}
            >
              {tick.change_pct >= 0 ? "+" : ""}
              {tick.change_pct.toFixed(2)}%
            </span>
            <span className="text-right text-gray-500">
              {tick.volume >= 1000 ? `${(tick.volume / 1000).toFixed(1)}K` : tick.volume}
            </span>
          </div>
        ))}
      </div>

      {/* Asset Tear Sheet Drawer */}
      <AssetTearSheet
        open={selectedTick !== null}
        onClose={() => setSelectedTick(null)}
        symbol={selectedTick?.symbol ?? ""}
        tickerId={selectedTick?.ticker_id ?? ""}
        currentPrice={selectedTick?.price ?? 0}
      />
    </div>
  );
}
