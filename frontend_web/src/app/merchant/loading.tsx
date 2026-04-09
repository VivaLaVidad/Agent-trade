"use client";

import { motion } from "framer-motion";

export default function MerchantLoading() {
  return (
    <div className="h-screen bg-[#0a0a0a] flex items-center justify-center">
      <div className="text-center">
        <motion.div
          className="w-10 h-10 rounded-full border-2 border-emerald-500 border-t-transparent mx-auto mb-4"
          animate={{ rotate: 360 }}
          transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
        />
        <div className="text-zinc-500 font-mono text-xs tracking-widest uppercase">
          Connecting to Trading Desk...
        </div>
        <div className="mt-3 flex gap-2 justify-center font-mono text-[10px] text-zinc-600">
          <span>FEED</span>
          <motion.span
            animate={{ opacity: [0.3, 1, 0.3] }}
            transition={{ duration: 1.5, repeat: Infinity }}
            className="text-amber-500"
          >
            SYNCING
          </motion.span>
        </div>
      </div>
    </div>
  );
}
