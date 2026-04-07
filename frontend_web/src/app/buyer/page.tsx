"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import type { StreamEvent } from "@/lib/api/types";
import { simulateSSEStream, MOCK_PROFORMA } from "@/lib/api/mock-data";
import { BuyerChatInput } from "@/components/buyer/BuyerChatInput";
import { QuickPromptTags } from "@/components/buyer/QuickPromptTags";
import { RadarScanner } from "@/components/buyer/RadarScanner";
import { TypewriterStream } from "@/components/buyer/TypewriterStream";
import { ProformaInvoiceCard } from "@/components/buyer/ProformaInvoiceCard";

type Phase = "idle" | "scanning" | "streaming" | "invoice" | "confirmed";

export default function BuyerPage() {
  const [inputValue, setInputValue] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [showInvoice, setShowInvoice] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const handleSubmit = useCallback((text: string) => {
    setInputValue(text);
    setPhase("scanning");
    setEvents([]);
    setShowInvoice(false);

    // After radar scan, start streaming
    setTimeout(() => {
      setPhase("streaming");

      const cancel = simulateSSEStream(
        (event) => {
          setEvents((prev) => [...prev, event]);
          // When docuforge completes, show invoice
          if (event.node === "docuforge" && event.status === "completed") {
            setTimeout(() => setShowInvoice(true), 500);
          }
        },
        () => {
          setPhase("invoice");
        },
      );

      // Store cancel in case we need it
      return cancel;
    }, 2500);
  }, []);

  const handleConfirm = useCallback(() => {
    setConfirming(true);
    setTimeout(() => {
      setConfirming(false);
      setPhase("confirmed");
    }, 2000);
  }, []);

  const isProcessing = phase !== "idle" && phase !== "confirmed";

  return (
    <div className="min-h-screen bg-gradient-to-b from-[#fafafa] to-[#f0f0f0]">
      {/* Header */}
      <header className="flex items-center justify-between px-8 py-5">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="p-2 rounded-lg hover:bg-gray-100 transition-colors"
            aria-label="Back to home"
          >
            <ArrowLeft className="w-5 h-5 text-gray-400" />
          </Link>
          <div>
            <h1 className="text-lg font-light tracking-tight text-gray-900">
              Trade<span className="font-semibold">Stealth</span>
            </h1>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400 font-mono">Buyer Portal</span>
        </div>
      </header>

      {/* Main content */}
      <main className="max-w-2xl mx-auto px-6 pt-12 pb-24">
        {/* Glass card */}
        <motion.div
          layout
          className="glass rounded-3xl shadow-2xl p-8 md:p-10"
        >
          {/* Title */}
          <motion.h2
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="text-3xl md:text-4xl font-light tracking-tight text-gray-800 mb-8"
          >
            What do you need today?
          </motion.h2>

          {/* Input */}
          <BuyerChatInput
            value={inputValue}
            onChange={setInputValue}
            onSubmit={handleSubmit}
            disabled={isProcessing}
          />

          {/* Quick prompts */}
          {phase === "idle" && (
            <QuickPromptTags
              onSelect={(prompt) => {
                setInputValue(prompt);
                handleSubmit(prompt);
              }}
              disabled={isProcessing}
            />
          )}

          {/* Response area */}
          <AnimatePresence mode="wait">
            {phase === "scanning" && (
              <motion.div
                key="scanner"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="mt-6"
              >
                <RadarScanner />
              </motion.div>
            )}

            {(phase === "streaming" || phase === "invoice") && (
              <motion.div
                key="stream"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                className="mt-6"
              >
                <div className="glass rounded-2xl p-5">
                  <TypewriterStream events={events} />
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Invoice card */}
          <AnimatePresence>
            {showInvoice && phase !== "confirmed" && (
              <motion.div
                key="invoice"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="mt-6"
              >
                <ProformaInvoiceCard
                  invoice={MOCK_PROFORMA}
                  onConfirm={handleConfirm}
                  confirming={confirming}
                />
              </motion.div>
            )}
          </AnimatePresence>

          {/* Confirmed state */}
          <AnimatePresence>
            {phase === "confirmed" && (
              <motion.div
                key="confirmed"
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                className="mt-8 text-center py-8"
              >
                <motion.div
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ type: "spring", damping: 10 }}
                  className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-emerald-100 mb-4"
                >
                  <span className="text-3xl">✓</span>
                </motion.div>
                <h3 className="text-xl font-semibold text-gray-900 mb-2">Order Confirmed</h3>
                <p className="text-sm text-gray-500">
                  PI-2026-0417 has been confirmed. You will receive payment instructions shortly.
                </p>
                <button
                  onClick={() => {
                    setPhase("idle");
                    setInputValue("");
                    setEvents([]);
                    setShowInvoice(false);
                  }}
                  className="mt-6 px-6 py-2 text-sm text-blue-600 hover:bg-blue-50 rounded-lg transition-colors"
                >
                  Start New Inquiry
                </button>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </main>
    </div>
  );
}
