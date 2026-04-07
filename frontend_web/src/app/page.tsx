"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ShoppingCart, BarChart3, Globe } from "lucide-react";

const portals = [
  {
    href: "/buyer",
    title: "Buyer Portal",
    description: "AI-powered procurement — describe what you need, we handle the rest",
    icon: ShoppingCart,
    gradient: "from-blue-500 to-cyan-400",
    bg: "bg-blue-500/10 hover:bg-blue-500/20",
  },
  {
    href: "/merchant",
    title: "Arbitrage Desk",
    description: "Bloomberg-style trading terminal for real-time hedge monitoring",
    icon: BarChart3,
    gradient: "from-emerald-400 to-green-500",
    bg: "bg-emerald-500/10 hover:bg-emerald-500/20",
  },
  {
    href: "/admin",
    title: "God Mode",
    description: "Global command center with 3D trade routes and live audit stream",
    icon: Globe,
    gradient: "from-purple-500 to-pink-500",
    bg: "bg-purple-500/10 hover:bg-purple-500/20",
  },
];

export default function HomePage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-[#fafafa] to-[#f0f0f0] flex flex-col items-center justify-center px-6">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
        className="text-center mb-16"
      >
        <h1 className="text-5xl font-light tracking-tight text-gray-900 mb-4">
          Trade<span className="font-semibold">Stealth</span>
        </h1>
        <p className="text-lg text-gray-500 max-w-md mx-auto">
          Industrial-grade AI trade orchestration platform
        </p>
      </motion.div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 max-w-4xl w-full">
        {portals.map((portal, i) => (
          <motion.div
            key={portal.href}
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.15 * i }}
          >
            <Link href={portal.href}>
              <div
                className={`group relative rounded-2xl border border-gray-200/60 p-8 transition-all duration-300 ${portal.bg} cursor-pointer`}
              >
                <div
                  className={`inline-flex p-3 rounded-xl bg-gradient-to-br ${portal.gradient} mb-5`}
                >
                  <portal.icon className="w-6 h-6 text-white" />
                </div>
                <h2 className="text-xl font-semibold text-gray-900 mb-2">
                  {portal.title}
                </h2>
                <p className="text-sm text-gray-500 leading-relaxed">
                  {portal.description}
                </p>
                <div className="mt-4 text-sm font-medium text-gray-400 group-hover:text-gray-600 transition-colors">
                  Enter →
                </div>
              </div>
            </Link>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
