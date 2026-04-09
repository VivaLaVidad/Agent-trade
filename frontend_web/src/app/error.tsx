"use client";

import Link from "next/link";
import { AlertTriangle } from "lucide-react";

export default function HomeError({ reset }: { reset: () => void }) {
  return (
    <div className="min-h-screen bg-zinc-950 flex items-center justify-center px-6">
      <div className="max-w-md w-full border border-zinc-800 bg-zinc-900/70 p-6 rounded-sm text-center">
        <AlertTriangle className="w-10 h-10 text-amber-400 mx-auto mb-4" />
        <h1 className="text-white text-xl mb-2">Terminal Initialization Failure</h1>
        <p className="text-zinc-400 text-sm mb-6 font-mono">
          The OmniEdge landing terminal failed to load. Retry the session or navigate to a dedicated portal.
        </p>
        <div className="flex gap-3 justify-center">
          <button
            onClick={reset}
            className="px-4 py-2 bg-emerald-500 text-black text-sm font-medium rounded-sm"
          >
            Retry
          </button>
          <Link href="/buyer" className="px-4 py-2 border border-zinc-700 text-zinc-200 text-sm rounded-sm">
            Open Buyer Portal
          </Link>
        </div>
      </div>
    </div>
  );
}
