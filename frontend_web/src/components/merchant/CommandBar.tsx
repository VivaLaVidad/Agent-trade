"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Terminal } from "lucide-react";

interface CommandResult {
  id: number;
  type: "success" | "error";
  message: string;
}

interface CommandBarProps {
  onOverride?: (tradeId: string, margin: number) => void;
  onKill?: (tradeId: string) => void;
}

let resultId = 0;

export function CommandBar({ onOverride, onKill }: CommandBarProps) {
  const [input, setInput] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [results, setResults] = useState<CommandResult[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const showResult = useCallback((type: "success" | "error", message: string) => {
    const id = ++resultId;
    setResults((prev) => [{ id, type, message }, ...prev].slice(0, 5));
    setTimeout(() => {
      setResults((prev) => prev.filter((r) => r.id !== id));
    }, 3000);
  }, []);

  const executeCommand = useCallback(
    (cmd: string) => {
      const trimmed = cmd.trim().toUpperCase();
      if (!trimmed) return;

      setHistory((prev) => [cmd, ...prev].slice(0, 50));
      setHistoryIndex(-1);

      // Parse OVRD <trade_id> <margin>
      const ovrdMatch = trimmed.match(/^OVRD\s+([\w-]+)\s+([\d.]+)$/);
      if (ovrdMatch) {
        const tradeId = ovrdMatch[1];
        const margin = parseFloat(ovrdMatch[2]);
        if (isNaN(margin) || margin <= 0) {
          showResult("error", `INVALID MARGIN: ${ovrdMatch[2]}`);
          return;
        }
        onOverride?.(tradeId, margin);
        showResult("success", `OVERRIDE EXECUTED — ${tradeId} @ ${margin}% margin`);
        return;
      }

      // Parse KILL <trade_id>
      const killMatch = trimmed.match(/^KILL\s+([\w-]+)$/);
      if (killMatch) {
        const tradeId = killMatch[1];
        onKill?.(tradeId);
        showResult("success", `TRADE TERMINATED — ${tradeId}`);
        return;
      }

      // Unknown command
      showResult("error", `UNKNOWN COMMAND: ${trimmed.split(" ")[0]} — try OVRD or KILL`);
    },
    [onOverride, onKill, showResult],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      executeCommand(input);
      setInput("");
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (history.length > 0) {
        const nextIndex = Math.min(historyIndex + 1, history.length - 1);
        setHistoryIndex(nextIndex);
        setInput(history[nextIndex]);
      }
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (historyIndex > 0) {
        const nextIndex = historyIndex - 1;
        setHistoryIndex(nextIndex);
        setInput(history[nextIndex]);
      } else {
        setHistoryIndex(-1);
        setInput("");
      }
    }
  };

  return (
    <div className="fixed bottom-0 left-0 right-0 z-50">
      {/* Flash feedback */}
      <AnimatePresence>
        {results.map((result) => (
          <motion.div
            key={result.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className={`mx-auto max-w-2xl px-4 py-1.5 mb-1 text-[11px] font-mono text-center rounded ${
              result.type === "success"
                ? "bg-[#00ff88]/15 text-[#00ff88] border border-[#00ff88]/30"
                : "bg-[#ff0044]/15 text-[#ff0044] border border-[#ff0044]/30"
            }`}
          >
            {result.message}
          </motion.div>
        ))}
      </AnimatePresence>

      {/* Command input */}
      <div className="bg-[#0a0a0a] border-t border-[#1a1a1a] px-4 py-2">
        <div className="flex items-center gap-3 max-w-full">
          <Terminal className="w-4 h-4 text-[#00ff88] flex-shrink-0" />
          <span className="text-[#00ff88] text-[11px] font-mono flex-shrink-0">$</span>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="OVRD TRD-7821 4.5  |  KILL TRD-7821"
            className="flex-1 bg-transparent text-[#00ff88] text-[12px] font-mono placeholder-[#00ff88]/30 focus:outline-none caret-[#00ff88]"
            spellCheck={false}
            autoComplete="off"
            aria-label="Command bar input"
          />
          <span className="text-[9px] font-mono text-gray-600 flex-shrink-0">
            {history.length > 0 ? `${history.length} cmds` : ""}
          </span>
        </div>
      </div>
    </div>
  );
}
