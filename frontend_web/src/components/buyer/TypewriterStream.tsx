"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { StreamEvent, AgentNode } from "@/lib/api/types";

const NODE_COLORS: Record<AgentNode, string> = {
  intent_clarifier: "bg-blue-500",
  supply_miner: "bg-emerald-500",
  hedge_engine: "bg-amber-500",
  regguard: "bg-red-500",
  docuforge: "bg-purple-500",
  negotiator: "bg-cyan-500",
  matching: "bg-indigo-500",
  ledger: "bg-teal-500",
};

const NODE_LABELS: Record<AgentNode, string> = {
  intent_clarifier: "Intent AI",
  supply_miner: "Supply Miner",
  hedge_engine: "Hedge Engine",
  regguard: "RegGuard",
  docuforge: "DocuForge",
  negotiator: "Negotiator",
  matching: "Matching",
  ledger: "Ledger",
};

interface TypewriterStreamProps {
  events: StreamEvent[];
}

export function TypewriterStream({ events }: TypewriterStreamProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [events.length]);

  return (
    <div ref={scrollRef} className="max-h-80 overflow-y-auto space-y-3 pr-2">
      <AnimatePresence mode="popLayout">
        {events.map((event, i) => (
          <motion.div
            key={`${event.node}-${event.status}-${i}`}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.3 }}
            className="flex items-start gap-3"
          >
            <span
              className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-mono font-semibold text-white tracking-wide ${NODE_COLORS[event.node]}`}
            >
              {NODE_LABELS[event.node]}
            </span>
            <TypewriterText text={event.message} />
            {event.status === "completed" && (
              <span className="text-emerald-500 text-xs mt-0.5 flex-shrink-0">✓</span>
            )}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function TypewriterText({ text }: { text: string }) {
  const [displayed, setDisplayed] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    setDisplayed("");
    setDone(false);
    let idx = 0;
    const interval = setInterval(() => {
      idx++;
      if (idx >= text.length) {
        setDisplayed(text);
        setDone(true);
        clearInterval(interval);
      } else {
        setDisplayed(text.slice(0, idx));
      }
    }, 18);
    return () => clearInterval(interval);
  }, [text]);

  return (
    <p className="text-sm text-gray-600 leading-relaxed flex-1">
      {displayed}
      {!done && <span className="inline-block w-1.5 h-4 bg-blue-500 ml-0.5 animate-blink-cursor" />}
    </p>
  );
}
