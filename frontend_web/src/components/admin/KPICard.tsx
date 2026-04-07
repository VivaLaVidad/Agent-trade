"use client";

import { useRef } from "react";
import { motion, useInView } from "framer-motion";
import CountUp from "react-countup";

interface KPICardProps {
  title: string;
  value: number;
  suffix?: string;
  decimals?: number;
  gradient: string;
  children?: React.ReactNode;
}

export function KPICard({ title, value, suffix, decimals = 0, gradient, children }: KPICardProps) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true });

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ borderColor: "rgba(255,255,255,0.2)" }}
      className="glass-dark rounded-2xl p-6 transition-all duration-300 hover:shadow-lg hover:shadow-white/5"
    >
      <p className="text-[10px] font-mono text-gray-500 tracking-widest uppercase mb-3">
        {title}
      </p>
      <div className="flex items-baseline gap-2 mb-4">
        <span className={`text-5xl font-bold bg-gradient-to-r ${gradient} bg-clip-text text-transparent`}>
          {inView ? (
            <CountUp end={value} duration={2.5} decimals={decimals} separator="," />
          ) : (
            "0"
          )}
        </span>
        {suffix && (
          <span className="text-lg text-gray-500 font-mono">{suffix}</span>
        )}
      </div>
      {children}
    </motion.div>
  );
}
