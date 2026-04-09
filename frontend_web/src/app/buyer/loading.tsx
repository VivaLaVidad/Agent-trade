"use client";

import { motion } from "framer-motion";

export default function BuyerLoading() {
  return (
    <div className="min-h-screen bg-[#0a0e14] flex items-center justify-center">
      <div className="text-center">
        <motion.div
          className="w-10 h-10 rounded-full border-2 border-blue-500 border-t-transparent mx-auto mb-4"
          animate={{ rotate: 360 }}
          transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
        />
        <div className="text-zinc-500 font-mono text-xs tracking-widest uppercase">
          Loading Procurement Terminal...
        </div>
      </div>
    </div>
  );
}
