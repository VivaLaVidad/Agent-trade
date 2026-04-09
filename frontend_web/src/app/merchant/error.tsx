"use client";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";

export default function MerchantError({ reset }: { reset: () => void }) {
  return (
    <div className="h-screen bg-[#0a0a0a] flex items-center justify-center px-6">
      <div className="max-w-md w-full border border-[#1f1f1f] bg-[#111111] p-6 rounded-sm text-center font-mono">
        <AlertTriangle className="w-10 h-10 text-red-400 mx-auto mb-4" />
        <h1 className="text-white text-lg mb-2 tracking-wide">TRADING DESK ERROR</h1>
        <p className="text-zinc-400 text-xs mb-6 leading-relaxed">
          Market desk session interrupted. Attempt a clean reconnect or route back to the main terminal.
        </p>
        <div className="flex gap-3 justify-center">
          <button onClick={reset} className="px-4 py-2 bg-emerald-500 text-black text-xs rounded-sm">
            RECONNECT
          </button>
          <Link href="/" className="px-4 py-2 border border-zinc-700 text-zinc-200 text-xs rounded-sm">
            EXIT
          </Link>
        </div>
      </div>
    </div>
  );
}
