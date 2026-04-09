"use client";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";

export default function BuyerError({ reset }: { reset: () => void }) {
  return (
    <div className="min-h-screen bg-[#0a0e14] flex items-center justify-center px-6">
      <div className="max-w-md w-full border border-white/10 bg-white/5 p-6 rounded-2xl text-center">
        <AlertTriangle className="w-10 h-10 text-amber-400 mx-auto mb-4" />
        <h1 className="text-white text-xl mb-2">Buyer Portal Offline</h1>
        <p className="text-zinc-400 text-sm mb-6">
          Procurement search is temporarily unavailable. Retry or return to the command deck.
        </p>
        <div className="flex gap-3 justify-center">
          <button onClick={reset} className="px-4 py-2 bg-blue-500 text-white text-sm rounded-xl">
            Retry
          </button>
          <Link href="/" className="px-4 py-2 border border-white/10 text-zinc-200 text-sm rounded-xl">
            Back Home
          </Link>
        </div>
      </div>
    </div>
  );
}
