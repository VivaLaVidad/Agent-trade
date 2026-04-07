"use client";

import { useState, useEffect, useCallback, forwardRef, useImperativeHandle } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Shield } from "lucide-react";
import type { PendingReview } from "@/lib/api/types";
import { startReviewStream } from "@/lib/api/mock-data";

type ReviewState = PendingReview & { decision?: "accepted" | "rejected" };

export interface HITLOverrideHandle {
  accept: (tradeId: string) => void;
  reject: (tradeId: string) => void;
}

export const HITLOverridePanel = forwardRef<HITLOverrideHandle>(function HITLOverridePanel(_props, ref) {
  const [reviews, setReviews] = useState<ReviewState[]>([]);

  const addReview = useCallback((review: PendingReview) => {
    setReviews((prev) => [{ ...review }, ...prev]);
  }, []);

  useEffect(() => {
    const stop = startReviewStream(addReview);
    return stop;
  }, [addReview]);

  const pendingCount = reviews.filter((r) => !r.decision).length;

  const handleDecision = useCallback((tradeId: string, decision: "accepted" | "rejected") => {
    setReviews((prev) =>
      prev.map((r) =>
        r.trade_id === tradeId && !r.decision ? { ...r, decision } : r,
      ),
    );
    // Remove after animation
    setTimeout(() => {
      setReviews((prev) => prev.filter((r) => r.trade_id !== tradeId || !r.decision));
    }, 1500);
  }, []);

  // Expose accept/reject to parent via ref (for CommandBar integration)
  useImperativeHandle(ref, () => ({
    accept: (tradeId: string) => handleDecision(tradeId, "accepted"),
    reject: (tradeId: string) => handleDecision(tradeId, "rejected"),
  }), [handleDecision]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div
        className={`flex items-center justify-between px-4 py-2 border-b transition-colors ${
          pendingCount > 0
            ? "border-[#ffaa00]/50 animate-amber-pulse"
            : "border-[#1a1a1a]"
        }`}
      >
        <span className="text-[11px] font-mono font-semibold text-gray-300 tracking-wider">
          HITL OVERRIDE
        </span>
        {pendingCount > 0 && (
          <span className="px-2 py-0.5 text-[10px] font-mono font-bold bg-[#ffaa00]/20 text-[#ffaa00] rounded">
            {pendingCount} PENDING
          </span>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto bloomberg-scroll p-3">
        <AnimatePresence mode="popLayout">
          {pendingCount === 0 && (
            <motion.div
              key="empty"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex flex-col items-center justify-center h-full gap-3 text-gray-700"
            >
              <Shield className="w-12 h-12 opacity-30" />
              <span className="text-[11px] font-mono tracking-wider">NO PENDING REVIEWS</span>
            </motion.div>
          )}

          {reviews
            .filter((r) => !r.decision)
            .map((review) => (
              <ReviewCard
                key={review.trade_id + review.timestamp}
                review={review}
                onAccept={() => handleDecision(review.trade_id, "accepted")}
                onReject={() => handleDecision(review.trade_id, "rejected")}
              />
            ))}

          {reviews
            .filter((r) => r.decision)
            .map((review) => (
              <motion.div
                key={`decided-${review.trade_id}`}
                initial={{ opacity: 1 }}
                animate={{ opacity: 0, height: 0 }}
                transition={{ duration: 1 }}
                className="overflow-hidden"
              >
                <div
                  className={`rounded-lg p-3 text-center text-[11px] font-mono font-bold ${
                    review.decision === "accepted"
                      ? "bg-[#00ff88]/10 text-[#00ff88]"
                      : "bg-[#ff0044]/10 text-[#ff0044]"
                  }`}
                >
                  {review.decision === "accepted" ? "CONFIRMED ✓" : "KILLED ✗"}
                </div>
              </motion.div>
            ))}
        </AnimatePresence>
      </div>
    </div>
  );
});

function ReviewCard({
  review,
  onAccept,
  onReject,
}: {
  review: PendingReview;
  onAccept: () => void;
  onReject: () => void;
}) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -20, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9 }}
      className="bg-[#111] border border-[#ffaa00]/30 rounded-lg p-4 mb-3"
    >
      {/* Trade ID */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] font-mono text-gray-500">{review.trade_id}</span>
        <span className="text-[10px] font-mono text-gray-600">
          {new Date(review.timestamp).toLocaleTimeString("en-US", { hour12: false })}
        </span>
      </div>

      {/* Profit margin — big number */}
      <div className="text-center mb-3">
        <span className="text-3xl font-mono font-bold text-[#ffaa00]">
          {review.profit_margin_pct.toFixed(1)}%
        </span>
        <p className="text-[9px] font-mono text-gray-600 mt-1">PROFIT MARGIN (GREY ZONE)</p>
      </div>

      {/* Details */}
      <div className="grid grid-cols-2 gap-2 text-[10px] font-mono mb-4">
        <div>
          <span className="text-gray-600">Buyer</span>
          <p className="text-gray-300">{review.buyer_name}</p>
        </div>
        <div>
          <span className="text-gray-600">Country</span>
          <p className="text-gray-300">{review.buyer_country}</p>
        </div>
        <div>
          <span className="text-gray-600">Product</span>
          <p className="text-gray-300 truncate">{review.product}</p>
        </div>
        <div>
          <span className="text-gray-600">Risk Score</span>
          <p className={`${review.risk_score > 30 ? "text-[#ffaa00]" : "text-gray-300"}`}>
            {review.risk_score}/100
          </p>
        </div>
        <div>
          <span className="text-gray-600">Qty</span>
          <p className="text-gray-300">{review.quantity.toLocaleString()}</p>
        </div>
        <div>
          <span className="text-gray-600">Price</span>
          <p className="text-gray-300">${review.quoted_price_usd.toFixed(4)}/unit</p>
        </div>
      </div>

      {/* Action buttons */}
      <div className="grid grid-cols-2 gap-2">
        <motion.button
          whileTap={{ scale: 0.95 }}
          onClick={onAccept}
          className="py-2.5 rounded-md bg-[#00ff88] text-black text-[11px] font-mono font-bold tracking-wider hover:bg-[#00ff88]/90 transition-colors"
          aria-label={`Accept trade ${review.trade_id}`}
        >
          ACCEPT (OVRD)
        </motion.button>
        <motion.button
          whileTap={{ scale: 0.95 }}
          onClick={onReject}
          className="py-2.5 rounded-md bg-[#ff0044] text-white text-[11px] font-mono font-bold tracking-wider hover:bg-[#ff0044]/90 transition-colors"
          aria-label={`Reject trade ${review.trade_id}`}
        >
          REJECT (KILL)
        </motion.button>
      </div>
    </motion.div>
  );
}
