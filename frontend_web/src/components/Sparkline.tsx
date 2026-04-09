"use client";

interface SparklineProps {
  data: number[];
  width?: number;
  height?: number;
  className?: string;
}

export function Sparkline({ data, width = 120, height = 32, className = "" }: SparklineProps) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = width / (data.length - 1);

  const points = data.map((v, i) => `${i * step},${height - ((v - min) / range) * (height - 4) - 2}`).join(" ");
  const areaPoints = `0,${height} ${points} ${width},${height}`;
  const trending = data[data.length - 1] >= data[0];
  const stroke = trending ? "#00ff88" : "#ff0044";
  const fill = trending ? "url(#sparkGreen)" : "url(#sparkRed)";

  return (
    <svg width={width} height={height} className={className} viewBox={`0 0 ${width} ${height}`}>
      <defs>
        <linearGradient id="sparkGreen" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#00ff88" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#00ff88" stopOpacity="0" />
        </linearGradient>
        <linearGradient id="sparkRed" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ff0044" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#ff0044" stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill={fill} />
      <polyline points={points} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
