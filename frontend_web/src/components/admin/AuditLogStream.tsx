"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Lock, Search, Fingerprint } from "lucide-react";
import type { AuditLogEntry, LogLevel } from "@/lib/api/types";
import { startAuditLogStream } from "@/lib/api/mock-data";

const LEVEL_STYLES: Record<LogLevel, string> = {
  DEBUG: "bg-gray-700 text-gray-300",
  INFO: "bg-blue-900/50 text-blue-400",
  WARN: "bg-yellow-900/50 text-yellow-400",
  ERROR: "bg-red-900/50 text-red-400",
  CRITICAL: "bg-red-800/70 text-red-300",
};

const LEVELS: LogLevel[] = ["INFO", "WARN", "ERROR", "CRITICAL", "DEBUG"];

interface AuditLogStreamProps {
  authorized: boolean;
}

// Generate a deterministic-looking SHA-256 hash from a string
function generateMockHash(input: string): string {
  let hash = "";
  const chars = "0123456789abcdef";
  let seed = 0;
  for (let i = 0; i < input.length; i++) {
    seed = ((seed << 5) - seed + input.charCodeAt(i)) | 0;
  }
  for (let i = 0; i < 64; i++) {
    seed = ((seed * 1103515245 + 12345) & 0x7fffffff);
    hash += chars[seed % 16];
  }
  return hash;
}

// Typewriter verification component
function VerificationPanel({ logEntry, onComplete }: { logEntry: AuditLogEntry; onComplete?: () => void }) {
  const [stage, setStage] = useState(0);
  const [typedHash, setTypedHash] = useState("");
  const fullHash = generateMockHash(logEntry.id + logEntry.message);

  useEffect(() => {
    // Stage 0: "Decrypting audit record..."
    const timer1 = setTimeout(() => setStage(1), 1500);
    return () => clearTimeout(timer1);
  }, []);

  useEffect(() => {
    if (stage !== 1) return;
    // Typewriter effect for hash
    let idx = 0;
    const interval = setInterval(() => {
      idx++;
      setTypedHash(fullHash.slice(0, idx));
      if (idx >= fullHash.length) {
        clearInterval(interval);
        setTimeout(() => setStage(2), 400);
      }
    }, 25);
    return () => clearInterval(interval);
  }, [stage, fullHash]);

  useEffect(() => {
    if (stage === 2) {
      onComplete?.();
    }
  }, [stage, onComplete]);

  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      transition={{ duration: 0.3 }}
      className="overflow-hidden"
    >
      <div className="bg-[#0a0f0a] border border-[#00ff88]/20 rounded-md mx-4 my-2 p-3 font-mono">
        {/* Stage 0: Decrypting */}
        <div className="flex items-center gap-2 mb-1.5">
          <span className={`text-[10px] ${stage >= 1 ? "text-[#00ff88]" : "text-[#00ff88]/60 animate-pulse"}`}>
            {stage >= 1 ? "✓" : "⟳"} Decrypting audit record...
          </span>
        </div>

        {/* Stage 1: Hash reveal */}
        {stage >= 1 && (
          <div className="flex items-start gap-2 mb-1.5">
            <span className="text-[10px] text-gray-500 flex-shrink-0">SHA-256:</span>
            <span className="text-[10px] text-[#00ff88] break-all">
              {typedHash}
              {stage === 1 && <span className="animate-blink-cursor">█</span>}
            </span>
          </div>
        )}

        {/* Stage 2: Verified */}
        {stage >= 2 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex items-center gap-2"
          >
            <span className="text-[11px] font-bold text-[#00ff88]">
              Signature Verified: OK ✓
            </span>
          </motion.div>
        )}
      </div>
    </motion.div>
  );
}

export function AuditLogStream({ authorized }: AuditLogStreamProps) {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [filter, setFilter] = useState<LogLevel | "ALL">("ALL");
  const [search, setSearch] = useState("");
  const [verifyingId, setVerifyingId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const userScrolled = useRef(false);

  const addLog = useCallback((entry: AuditLogEntry) => {
    setLogs((prev) => [entry, ...prev].slice(0, 500));
  }, []);

  useEffect(() => {
    if (!authorized) return;
    const stop = startAuditLogStream(addLog);
    return stop;
  }, [authorized, addLog]);

  useEffect(() => {
    if (!userScrolled.current && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [logs.length]);

  if (!authorized) {
    return (
      <div className="flex flex-col items-center justify-center h-48 gap-3">
        <Lock className="w-8 h-8 text-gray-600" />
        <p className="text-[11px] font-mono text-gray-600 tracking-wider">
          ADMIN TOKEN REQUIRED
        </p>
      </div>
    );
  }

  const filtered = logs.filter((log) => {
    if (filter !== "ALL" && log.level !== filter) return false;
    if (search && !log.message.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/5">
        <div className="flex items-center gap-2">
          <Lock className="w-3 h-3 text-gray-600" />
          <span className="text-[11px] font-mono text-gray-400 tracking-wider">
            ENCRYPTED AUDIT LOG
          </span>
        </div>
        <span className="text-[10px] font-mono text-gray-600">{logs.length} entries</span>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-white/5">
        <div className="flex gap-1">
          <button
            onClick={() => setFilter("ALL")}
            className={`px-2 py-0.5 text-[9px] font-mono rounded transition-colors ${
              filter === "ALL" ? "bg-white/10 text-white" : "text-gray-600 hover:text-gray-400"
            }`}
          >
            ALL
          </button>
          {LEVELS.map((level) => (
            <button
              key={level}
              onClick={() => setFilter(level)}
              className={`px-2 py-0.5 text-[9px] font-mono rounded transition-colors ${
                filter === level ? "bg-white/10 text-white" : "text-gray-600 hover:text-gray-400"
              }`}
            >
              {level}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-600" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search..."
            className="pl-6 pr-2 py-1 text-[10px] font-mono bg-white/5 border border-white/10 rounded text-gray-300 placeholder-gray-600 focus:outline-none focus:border-white/20 w-40"
            aria-label="Search audit logs"
          />
        </div>
      </div>

      {/* Log entries */}
      <div
        ref={scrollRef}
        onScroll={() => {
          if (scrollRef.current) {
            userScrolled.current = scrollRef.current.scrollTop > 50;
          }
        }}
        className="flex-1 overflow-y-auto bloomberg-scroll"
      >
        <AnimatePresence initial={false}>
          {filtered.map((log) => (
            <div key={log.id}>
              <motion.div
                initial={{ opacity: 0, x: -5 }}
                animate={{ opacity: 1, x: 0 }}
                className="flex items-start gap-2 px-4 py-1.5 border-b border-white/[0.03] hover:bg-white/[0.02]"
              >
                <span className="text-[9px] font-mono text-gray-600 flex-shrink-0 mt-0.5">
                  {new Date(log.timestamp).toLocaleTimeString("en-US", { hour12: false })}
                </span>
                <span
                  className={`px-1.5 py-0 text-[8px] font-mono font-bold rounded flex-shrink-0 ${LEVEL_STYLES[log.level]}`}
                >
                  {log.level}
                </span>
                <span className="text-[9px] font-mono text-gray-500 flex-shrink-0">
                  [{log.source_module}]
                </span>
                <span className="text-[10px] font-mono text-gray-400 flex-1 break-words">
                  {log.encrypted ? "🔒 " : ""}
                  {log.message}
                </span>
                <button
                  onClick={() => setVerifyingId(verifyingId === log.id ? null : log.id)}
                  className={`flex-shrink-0 flex items-center gap-1 px-1.5 py-0.5 rounded text-[8px] font-mono transition-colors ${
                    verifyingId === log.id
                      ? "bg-[#00ff88]/20 text-[#00ff88]"
                      : "bg-white/5 text-gray-500 hover:text-[#00ff88] hover:bg-[#00ff88]/10"
                  }`}
                  aria-label={`Verify audit log ${log.id}`}
                >
                  <Fingerprint className="w-3 h-3" />
                  <span>Verify</span>
                </button>
              </motion.div>

              {/* Verification expansion */}
              <AnimatePresence>
                {verifyingId === log.id && (
                  <VerificationPanel key={`verify-${log.id}`} logEntry={log} />
                )}
              </AnimatePresence>
            </div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
