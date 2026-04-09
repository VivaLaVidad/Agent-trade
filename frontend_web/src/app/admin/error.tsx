"use client";

import Link from "next/link";
import { ShieldAlert } from "lucide-react";

export default function AdminError({ reset }: { reset: () => void }) {
  return (
    <div className="min-h-screen bg-gradient-to-b from-[#050a15] to-[#0a1628] flex items-center justify-center px-6">
      <div className="max-w-md w-full border border-white/10 bg-[#0f1725]/80 p-6 rounded-2xl text-center">
        <ShieldAlert className="w-10 h-10 text-pink-400 mx-auto mb-4" />
        <h1 className="text-white text-xl mb-2">Control Plane Disrupted</h1>
        <p className="text-zinc-400 text-sm mb-6">
          Admin telemetry failed to render. Reinitialize the panel or fall back to the landing terminal.
        </p>
        <div className="flex gap-3 justify-center">
          <button onClick={reset} className="px-4 py-2 bg-purple-500 text-white text-sm rounded-xl">
            Reinitialize
          </button>
          <Link href="/" className="px-4 py-2 border border-white/10 text-zinc-200 text-sm rounded-xl">
            Return Home
          </Link>
        </div>
      </div>
    </div>
  );
}
