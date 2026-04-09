"use client";

import { motion } from "framer-motion";

export default function AdminLoading() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-[#050a15] to-[#0a1628] flex items-center justify-center">
      <div className="text-center">
        <motion.div
          className="w-10 h-10 rounded-full border-2 border-purple-500 border-t-transparent mx-auto mb-4"
          animate={{ rotate: 360 }}
          transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
        />
        <div className="text-zinc-500 font-mono text-xs tracking-widest uppercase">
          Authenticating Operator...
        </div>
        <div className="mt-3 w-48 h-1 bg-zinc-800 rounded-full overflow-hidden mx-auto">
          <motion.div
            className="h-full bg-gradient-to-r from-purple-500 to-pink-500"
            animate={{ width: ["0%", "100%"] }}
            transition={{ duration: 2, repeat: Infinity }}
          />
        </div>
      </div>
    </div>
  );
}
