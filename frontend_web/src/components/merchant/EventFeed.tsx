"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { MarketEvent, EventType } from "@/lib/api/types";
import { startEventStream } from "@/lib/api/mock-data";

const EVENT_STYLES: Record<EventType, { bg: string; text: string; label: string }> = {
  hedge_locked: { bg: "bg-[#00ff88]/20", text: "text-[#00ff88]", label: "HEDGE-LOCKED" },
  reg_denied: { bg: "bg-[#ff0044]/20", text: "text-[#ff0044]", label: "REG-DENIED" },
  pending_review: { bg: "bg-[#ffaa00]/20", text: "text-[#ffaa00]", label: "PENDING-REVIEW" },
  price_update: { bg: "bg-gray-800", text: "text-gray-400", label: "PRICE-UPD" },
  fx_tick: { bg: "bg-gray-800", text: "text-blue-400", label: "FX-TICK" },
  volatility_spike: { bg: "bg-orange-900/30", text: "text-orange-400", label: "VOL-SPIKE" },
  inventory_alert: { bg: "bg-yellow-900/20", text: "text-yellow-400", label: "INV-ALERT" },
  negotiation_update: { bg: "bg-cyan-900/20", text: "text-cyan-400", label: "NEG-UPDATE" },
  document_generated: { bg: "bg-purple-900/20", text: "text-purple-400", label: "DOC-GEN" },
};

const MAX_EVENTS = 200;

export function EventFeed() {
  const [events, setEvents] = useState<MarketEvent[]>([]);
  const [count, setCount] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const userScrolled = useRef(false);

  const addEvent = useCallback((event: MarketEvent) => {
    setEvents((prev) => [event, ...prev].slice(0, MAX_EVENTS));
    setCount((c) => c + 1);
  }, []);

  useEffect(() => {
    const stop = startEventStream(addEvent);
    return stop;
  }, [addEvent]);

  useEffect(() => {
    if (!userScrolled.current && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [events.length]);

  const handleScroll = () => {
    if (!scrollRef.current) return;
    userScrolled.current = scrollRef.current.scrollTop > 50;
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-[#1a1a1a]">
        <span className="text-[11px] font-mono font-semibold text-gray-300 tracking-wider">
          EVENT STREAM
        </span>
        <span className="text-[10px] font-mono text-gray-600">{count} msgs</span>
      </div>

      {/* Events */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto bloomberg-scroll"
      >
        <AnimatePresence initial={false}>
          {events.map((event) => {
            const style = EVENT_STYLES[event.event_type] ?? EVENT_STYLES.price_update;
            return (
              <motion.div
                key={event.event_id}
                initial={{ opacity: 0, x: 10 }}
                animate={{ opacity: 1, x: 0 }}
                className={`px-3 py-1.5 border-b border-[#111] ${
                  event.event_type === "hedge_locked" || event.event_type === "reg_denied"
                    ? style.bg
                    : ""
                }`}
              >
                <div className="flex items-center gap-2 mb-0.5">
                  <span className="text-[9px] font-mono text-gray-600">
                    {new Date(event.timestamp).toLocaleTimeString("en-US", { hour12: false })}
                  </span>
                  <span
                    className={`text-[9px] font-mono font-bold tracking-wider ${style.text}`}
                  >
                    [{style.label}]
                  </span>
                </div>
                <div className="text-[10px] font-mono text-gray-400 truncate">
                  {event.ticker_id}
                  {event.data && (
                    <span className="text-gray-600 ml-2">
                      {Object.entries(event.data)
                        .map(([k, v]) => `${k}=${v}`)
                        .join(" ")}
                    </span>
                  )}
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </div>
  );
}
