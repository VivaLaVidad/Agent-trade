"use client";

import Link from "next/link";
import { motion, useAnimation } from "framer-motion";
import { ShoppingCart, BarChart3, Globe, TrendingUp, TrendingDown, Activity } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { Sparkline } from "@/components/Sparkline";
import { TerminalLogViewer } from "@/components/TerminalLogViewer";
import { useEffect, useState, useRef } from "react";

// Mock ticker data for the tape
const TICKER_DATA = [
  { symbol: "CLAW-ELEC-5GCPE", price: 128.00, change: 2.3 },
  { symbol: "CLAW-MECH-CNC01", price: 4520.00, change: -0.8 },
  { symbol: "CLAW-TELE-5GANT", price: 890.50, change: 1.2 },
  { symbol: "CLAW-MINE-SAFETY", price: 2340.00, change: 0.5 },
  { symbol: "CLAW-SOLAR-PV500", price: 156.80, change: -1.1 },
  { symbol: "CLAW-INDU-ROBOT", price: 12450.00, change: 3.4 },
  { symbol: "CLAW-TRANS-LOGIS", price: 892.00, change: 0.2 },
  { symbol: "CLAW-CHEM-REACT", price: 3450.00, change: -0.3 },
];

// Particle background component
function ParticleBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // Respect prefers-reduced-motion
    const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReduced) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Mobile degradation: fewer particles, no connection lines
    const isMobile = window.innerWidth < 768;
    const particleCount = isMobile ? 20 : 60;
    const drawLines = !isMobile;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const particles: Array<{ x: number; y: number; vx: number; vy: number; size: number }> = [];

    const resize = () => {
      canvas.width = window.innerWidth * dpr;
      canvas.height = window.innerHeight * dpr;
      canvas.style.width = `${window.innerWidth}px`;
      canvas.style.height = `${window.innerHeight}px`;
      ctx.scale(dpr, dpr);
    };
    resize();
    window.addEventListener("resize", resize);

    for (let i = 0; i < particleCount; i++) {
      particles.push({
        x: Math.random() * window.innerWidth,
        y: Math.random() * window.innerHeight,
        vx: (Math.random() - 0.5) * 0.3,
        vy: (Math.random() - 0.5) * 0.3,
        size: Math.random() * 2 + 1,
      });
    }

    let animationId: number;
    const animate = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      particles.forEach((p, i) => {
        p.x += p.vx;
        p.y += p.vy;

        const w = window.innerWidth;
        const h = window.innerHeight;
        if (p.x < 0 || p.x > w) p.vx *= -1;
        if (p.y < 0 || p.y > h) p.vy *= -1;

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(0, 255, 136, 0.3)";
        ctx.fill();

        // Draw connections (skip on mobile for performance)
        if (drawLines) {
          particles.slice(i + 1).forEach((p2) => {
            const dx = p.x - p2.x;
            const dy = p.y - p2.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < 150) {
              ctx.beginPath();
              ctx.moveTo(p.x, p.y);
              ctx.lineTo(p2.x, p2.y);
              ctx.strokeStyle = `rgba(0, 255, 136, ${0.1 * (1 - dist / 150)})`;
              ctx.stroke();
            }
          });
        }
      });

      animationId = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      window.removeEventListener("resize", resize);
      cancelAnimationFrame(animationId);
    };
  }, []);

  return <canvas ref={canvasRef} className="fixed inset-0 pointer-events-none z-0" />;
}

// Ticker tape component
function TickerTape() {
  const [position, setPosition] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setPosition((prev) => (prev - 1) % 100);
    }, 30);
    return () => clearInterval(interval);
  }, []);

  const duplicatedData = [...TICKER_DATA, ...TICKER_DATA, ...TICKER_DATA];

  return (
    <div className="fixed bottom-0 left-0 right-0 z-40 bg-zinc-950/95 backdrop-blur-sm border-t border-zinc-800">
      <div className="overflow-hidden py-2">
        <motion.div
          className="flex gap-8 whitespace-nowrap"
          animate={{ x: position * 20 }}
          transition={{ type: "tween", ease: "linear", duration: 0 }}
        >
          {duplicatedData.map((item, i) => (
            <div key={`${item.symbol}-${i}`} className="flex items-center gap-2 text-xs font-mono">
              <span className="text-zinc-500">{item.symbol}</span>
              <span className="text-white">${item.price.toFixed(2)}</span>
              {item.change >= 0 ? (
                <TrendingUp className="w-3 h-3 text-emerald-400" />
              ) : (
                <TrendingDown className="w-3 h-3 text-rose-400" />
              )}
              <span className={item.change >= 0 ? "text-emerald-400" : "text-rose-400"}>
                {item.change >= 0 ? "+" : ""}
                {item.change.toFixed(1)}%
              </span>
            </div>
          ))}
        </motion.div>
      </div>
    </div>
  );
}

// Live stats component
function LiveStats() {
  const [stats, setStats] = useState({
    activeTrades: 142,
    volume: 2847500,
    suppliers: 89,
  });

  useEffect(() => {
    const interval = setInterval(() => {
      setStats((prev) => ({
        activeTrades: prev.activeTrades + (Math.random() > 0.5 ? 1 : -1),
        volume: prev.volume + Math.floor(Math.random() * 5000),
        suppliers: prev.suppliers,
      }));
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="flex gap-6 text-xs font-mono">
      <div className="flex items-center gap-2">
        <Activity className="w-3 h-3 text-emerald-400 animate-pulse" />
        <span className="text-zinc-500">TRADES:</span>
        <span className="text-white">{stats.activeTrades}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-zinc-500">VOL:</span>
        <span className="text-white">${(stats.volume / 1000000).toFixed(2)}M</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-zinc-500">SUPPLIERS:</span>
        <span className="text-white">{stats.suppliers}</span>
      </div>
    </div>
  );
}

export default function HomePage() {
  const { t } = useI18n();
  const [hoveredCard, setHoveredCard] = useState<string | null>(null);

  // Generate sparkline data for each portal
  const buyerSparkline = [100, 102, 98, 105, 110, 108, 115, 120, 118, 125];
  const merchantSparkline = [100, 95, 92, 98, 102, 105, 103, 108, 112, 115];
  const adminSparkline = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100];

  const portals = [
    {
      id: "buyer",
      href: "/buyer",
      titleKey: "home.buyer.title",
      descKey: "home.buyer.desc",
      icon: ShoppingCart,
      gradient: "from-blue-500 to-cyan-400",
      borderColor: "border-blue-500/30",
      hoverBorder: "hover:border-blue-400/60",
      sparkline: buyerSparkline,
      stat: "+12.5%",
      statLabel: "24h Vol",
    },
    {
      id: "merchant",
      href: "/merchant",
      titleKey: "home.merchant.title",
      descKey: "home.merchant.desc",
      icon: BarChart3,
      gradient: "from-emerald-400 to-green-500",
      borderColor: "border-emerald-500/30",
      hoverBorder: "hover:border-emerald-400/60",
      sparkline: merchantSparkline,
      stat: "+8.3%",
      statLabel: "Margin",
    },
    {
      id: "admin",
      href: "/admin",
      titleKey: "home.admin.title",
      descKey: "home.admin.desc",
      icon: Globe,
      gradient: "from-purple-500 to-pink-500",
      borderColor: "border-purple-500/30",
      hoverBorder: "hover:border-purple-400/60",
      sparkline: adminSparkline,
      stat: "99.9%",
      statLabel: "Uptime",
    },
  ];

  return (
    <div className="min-h-screen bg-zinc-950 flex flex-col relative overflow-hidden">
      <ParticleBackground />

      {/* Top bar */}
      <header className="relative z-20 flex items-center justify-between px-6 py-4 border-b border-zinc-800/50 bg-zinc-950/80 backdrop-blur-sm">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded bg-gradient-to-br from-emerald-400 to-green-500 flex items-center justify-center">
              <Activity className="w-5 h-5 text-white" />
            </div>
            <h1 className="text-xl font-light tracking-tight text-white">
              Omni<span className="font-semibold">Edge</span>
            </h1>
          </div>
          <div className="hidden md:block h-4 w-px bg-zinc-700" />
          <LiveStats />
        </div>
        <LanguageSwitcher />
      </header>

      {/* Main content */}
      <main className="relative z-10 flex-1 flex flex-col lg:flex-row gap-6 p-6 pb-20">
        {/* Left: Portal cards */}
        <div className="flex-1">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
            className="mb-6"
          >
            <h2 className="text-2xl font-light text-white mb-2">
              {t("brand.tagline")}
            </h2>
            <p className="text-sm text-zinc-500 font-mono">
              Select your terminal to begin
            </p>
          </motion.div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {portals.map((portal, i) => (
              <motion.div
                key={portal.href}
                initial={{ opacity: 0, y: 30 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.1 * i }}
                onHoverStart={() => setHoveredCard(portal.id)}
                onHoverEnd={() => setHoveredCard(null)}
              >
                <Link href={portal.href} prefetch={true}>
                  <div
                    className={`group relative h-full rounded-sm border ${portal.borderColor} ${portal.hoverBorder} bg-zinc-900/50 backdrop-blur-sm p-5 transition-all duration-300 cursor-pointer overflow-hidden`}
                  >
                    {/* Hover glow effect */}
                    {hoveredCard === portal.id && (
                      <motion.div
                        layoutId="hoverGlow"
                        className={`absolute inset-0 bg-gradient-to-br ${portal.gradient} opacity-5`}
                        transition={{ type: "spring", stiffness: 300, damping: 30 }}
                      />
                    )}

                    {/* Icon */}
                    <div className={`inline-flex p-2.5 rounded bg-gradient-to-br ${portal.gradient} mb-4`}>
                      <portal.icon className="w-5 h-5 text-white" />
                    </div>

                    {/* Title */}
                    <h3 className="text-lg font-semibold text-white mb-2">
                      {t(portal.titleKey)}
                    </h3>

                    {/* Description - fixed height */}
                    <p className="text-xs text-zinc-400 leading-relaxed mb-4 h-10 line-clamp-2">
                      {t(portal.descKey)}
                    </p>

                    {/* Sparkline */}
                    <div className="mb-3">
                      <Sparkline data={portal.sparkline} width={120} height={28} />
                    </div>

                    {/* Stat */}
                    <div className="flex items-center justify-between text-xs font-mono">
                      <span className="text-zinc-500">{portal.statLabel}</span>
                      <span className="text-emerald-400">{portal.stat}</span>
                    </div>

                    {/* Enter indicator */}
                    <div className="mt-4 pt-3 border-t border-zinc-800 flex items-center justify-between">
                      <span className="text-[10px] text-zinc-600 font-mono uppercase tracking-wider">
                        {t("home.enter")}
                      </span>
                      <motion.div
                        className="w-1.5 h-1.5 rounded-full bg-emerald-400"
                        animate={hoveredCard === portal.id ? { scale: [1, 1.5, 1] } : {}}
                        transition={{ duration: 0.5 }}
                      />
                    </div>
                  </div>
                </Link>
              </motion.div>
            ))}
          </div>
        </div>

        {/* Right: Terminal log viewer */}
        <div className="lg:w-96 h-64 lg:h-auto">
          <TerminalLogViewer />
        </div>
      </main>

      {/* Ticker tape */}
      <TickerTape />
    </div>
  );
}
