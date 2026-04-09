"use client";

import { useState, Suspense, lazy } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import { ArrowLeft, Lock, X } from "lucide-react";
import { KPICard } from "@/components/admin/KPICard";
import { AuditLogStream } from "@/components/admin/AuditLogStream";
import { MOCK_KPIS, MOCK_TRADE_ROUTES } from "@/lib/api/mock-data";
import { useI18n } from "@/lib/i18n";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";

const GlobeVisualization = lazy(() =>
  import("@/components/admin/GlobeVisualization").then((m) => ({
    default: m.GlobeVisualization,
  })),
);

export default function AdminPage() {
  const { t } = useI18n();
  const [authorized, setAuthorized] = useState(false);
  const [showTokenDialog, setShowTokenDialog] = useState(true);
  const [tokenInput, setTokenInput] = useState("");
  const [tokenError, setTokenError] = useState(false);

  const handleTokenSubmit = () => {
    if (tokenInput.trim().length >= 4) {
      setAuthorized(true);
      setShowTokenDialog(false);
      setTokenError(false);
    } else {
      setTokenError(true);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-[#050a15] to-[#0a1628] text-gray-200">
      {/* Particle background effect */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        {Array.from({ length: 30 }).map((_, i) => (
          <motion.div
            key={i}
            className="absolute w-1 h-1 rounded-full bg-white/10"
            initial={{
              x: `${Math.random() * 100}%`,
              y: `${Math.random() * 100}%`,
            }}
            animate={{
              x: `${Math.random() * 100}%`,
              y: `${Math.random() * 100}%`,
            }}
            transition={{
              duration: 20 + Math.random() * 30,
              repeat: Infinity,
              repeatType: "reverse",
              ease: "linear",
            }}
          />
        ))}
      </div>

      {/* Token Dialog */}
      <AnimatePresence>
        {showTokenDialog && !authorized && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              className="glass-dark rounded-2xl p-8 w-full max-w-sm mx-4"
            >
              <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                  <Lock className="w-5 h-5 text-gray-400" />
                  <h3 className="text-lg font-semibold text-gray-200">Admin Access</h3>
                </div>
                <button
                  onClick={() => setShowTokenDialog(false)}
                  className="p-1 rounded hover:bg-white/10 transition-colors"
                  aria-label="Close dialog"
                >
                  <X className="w-4 h-4 text-gray-500" />
                </button>
              </div>
              <p className="text-sm text-gray-500 mb-4">
                Enter your hardware token to access the dashboard.
              </p>
              <input
                type="password"
                value={tokenInput}
                onChange={(e) => {
                  setTokenInput(e.target.value);
                  setTokenError(false);
                }}
                onKeyDown={(e) => e.key === "Enter" && handleTokenSubmit()}
                placeholder="Hardware Token"
                className={`w-full px-4 py-3 rounded-xl bg-white/5 border text-sm font-mono text-gray-200 placeholder-gray-600 focus:outline-none transition-colors ${
                  tokenError ? "border-red-500/50" : "border-white/10 focus:border-white/20"
                }`}
                aria-label="Admin token input"
                autoFocus
              />
              {tokenError && (
                <p className="text-xs text-red-400 mt-2">Token must be at least 4 characters</p>
              )}
              <button
                onClick={handleTokenSubmit}
                className="w-full mt-4 py-3 rounded-xl bg-gradient-to-r from-blue-600 to-purple-600 text-white text-sm font-semibold hover:opacity-90 transition-opacity"
              >
                Authenticate
              </button>
              <p className="text-[10px] text-gray-600 text-center mt-3">
                Demo: enter any 4+ character token
              </p>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Header */}
      <header className="relative z-10 flex items-center justify-between px-6 py-4">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="p-2 rounded-lg hover:bg-white/5 transition-colors"
            aria-label="Back to home"
          >
            <ArrowLeft className="w-5 h-5 text-gray-500" />
          </Link>
          <div>
            <h1 className="text-sm font-mono font-bold text-gray-300 tracking-widest">
              {t("admin.title").toUpperCase()}
            </h1>
            <p className="text-[10px] font-mono text-gray-600">
              TradeForge {t("admin.title")}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {authorized ? (
            <span className="flex items-center gap-1.5 text-[10px] font-mono text-emerald-500">
              <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse-dot" />
              AUTHENTICATED
            </span>
          ) : (
            <button
              onClick={() => setShowTokenDialog(true)}
              className="flex items-center gap-1.5 text-[10px] font-mono text-gray-500 hover:text-gray-300 transition-colors"
            >
              <Lock className="w-3 h-3" />
              LOCKED
            </button>
          )}
          <LanguageSwitcher />
        </div>
      </header>

      {/* Main content */}
      <main className="relative z-10 px-6 pb-8 space-y-6">
        {/* KPI Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <KPICard
            title="Today's Inquiries"
            value={MOCK_KPIS.total_inquiries}
            gradient="from-blue-400 to-cyan-400"
          >
            <div className="flex items-end gap-1 h-8">
              {MOCK_KPIS.inquiries_trend.map((v, i) => (
                <div
                  key={i}
                  className="flex-1 bg-gradient-to-t from-blue-500/30 to-blue-400/10 rounded-sm"
                  style={{ height: `${(v / 150) * 100}%` }}
                />
              ))}
            </div>
          </KPICard>

          <KPICard
            title="Hedge Success"
            value={MOCK_KPIS.hedge_success}
            gradient="from-emerald-400 to-green-400"
          >
            <div className="flex items-center gap-3">
              <div className="relative w-10 h-10">
                <svg viewBox="0 0 36 36" className="w-full h-full -rotate-90">
                  <circle
                    cx="18" cy="18" r="15"
                    fill="none"
                    stroke="rgba(255,255,255,0.05)"
                    strokeWidth="3"
                  />
                  <circle
                    cx="18" cy="18" r="15"
                    fill="none"
                    stroke="#00ff88"
                    strokeWidth="3"
                    strokeDasharray={`${MOCK_KPIS.hedge_success_rate * 0.942} 94.2`}
                    strokeLinecap="round"
                  />
                </svg>
              </div>
              <span className="text-sm font-mono text-emerald-400">
                {MOCK_KPIS.hedge_success_rate}%
              </span>
            </div>
          </KPICard>

          <KPICard
            title="RegGuard Blocks"
            value={MOCK_KPIS.regguard_blocks}
            gradient="from-red-400 to-orange-400"
          >
            <div className="flex gap-2">
              {Object.entries(MOCK_KPIS.block_types).map(([type, count]) => (
                <div key={type} className="text-center">
                  <div className="text-[10px] font-mono text-gray-500">{type}</div>
                  <div className="text-xs font-mono text-red-400">{count}</div>
                </div>
              ))}
            </div>
          </KPICard>
        </div>

        {/* 3D Globe */}
        <div className="glass-dark rounded-2xl overflow-hidden">
          <Suspense
            fallback={
              <div className="flex items-center justify-center h-[400px]">
                <motion.div
                  animate={{ rotate: 360 }}
                  transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
                  className="w-8 h-8 border-2 border-blue-500/30 border-t-blue-500 rounded-full"
                />
              </div>
            }
          >
            <GlobeVisualization routes={MOCK_TRADE_ROUTES} />
          </Suspense>
        </div>

        {/* Audit Log */}
        <div className="glass-dark rounded-2xl overflow-hidden h-80">
          <AuditLogStream authorized={authorized} />
        </div>
      </main>
    </div>
  );
}
