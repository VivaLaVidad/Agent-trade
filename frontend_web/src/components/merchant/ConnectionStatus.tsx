"use client";

interface ConnectionStatusProps {
  connected: boolean;
}

export function ConnectionStatus({ connected }: ConnectionStatusProps) {
  return (
    <div className="flex items-center gap-2">
      <div
        className={`w-2 h-2 rounded-full ${
          connected
            ? "bg-[#00ff88] animate-pulse-dot shadow-[0_0_6px_#00ff88]"
            : "bg-[#ff0044] shadow-[0_0_6px_#ff0044]"
        }`}
      />
      <span className="text-[10px] font-mono text-gray-500 uppercase tracking-wider">
        {connected ? "LIVE" : "DISCONNECTED"}
      </span>
    </div>
  );
}
