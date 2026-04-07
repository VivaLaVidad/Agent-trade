"use client";

import { motion } from "framer-motion";

export function RadarScanner() {
  return (
    <div className="flex flex-col items-center justify-center py-10 gap-4">
      <div className="relative w-32 h-32">
        {/* Concentric rings */}
        {[1, 2, 3].map((ring) => (
          <motion.div
            key={ring}
            className="absolute inset-0 rounded-full border border-blue-300/40"
            initial={{ scale: 0.3, opacity: 0.8 }}
            animate={{ scale: ring * 0.6 + 0.4, opacity: 0 }}
            transition={{
              duration: 2,
              repeat: Infinity,
              delay: ring * 0.4,
              ease: "easeOut",
            }}
          />
        ))}
        {/* Center dot */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-3 h-3 rounded-full bg-blue-500 shadow-lg shadow-blue-500/50" />
        {/* Sweep line */}
        <motion.div
          className="absolute top-1/2 left-1/2 w-14 h-0.5 bg-gradient-to-r from-blue-500 to-transparent origin-left"
          animate={{ rotate: 360 }}
          transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
        />
      </div>
      <motion.p
        className="text-sm text-gray-400 tracking-wide"
        animate={{ opacity: [0.4, 1, 0.4] }}
        transition={{ duration: 2, repeat: Infinity }}
      >
        Scanning global suppliers...
      </motion.p>
    </div>
  );
}
