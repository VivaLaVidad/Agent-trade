import type {
  StreamEvent,
  ProformaInvoice,
  MarketTick,
  MarketEvent,
  PendingReview,
  DashboardKPIs,
  TradeRoute,
  AuditLogEntry,
} from "./types";

// ─── Buyer Portal: Simulated SSE Stream ─────────────────────

const AGENT_MESSAGES: StreamEvent[] = [
  {
    node: "intent_clarifier",
    status: "running",
    message: "Parsing buyer intent... Detected: MLCC Capacitors, 500pcs, CIF Mumbai",
    timestamp: Date.now(),
  },
  {
    node: "intent_clarifier",
    status: "completed",
    message: "Intent confirmed: product=capacitor, qty=500, incoterm=CIF, dest=Mumbai, India",
    timestamp: Date.now() + 1500,
  },
  {
    node: "supply_miner",
    status: "running",
    message: "Scanning 3 verified suppliers in Shenzhen & Dongguan industrial zones...",
    timestamp: Date.now() + 3000,
  },
  {
    node: "supply_miner",
    status: "completed",
    message: "Found 2 matching SKUs: CLAW-ELEC-CAP-100NF50V (Yageo), CLAW-ELEC-CAP-100NF50V (Samsung)",
    timestamp: Date.now() + 5000,
  },
  {
    node: "negotiator",
    status: "running",
    message: "Initiating price negotiation with top-ranked supplier (score: 0.92)...",
    timestamp: Date.now() + 6500,
  },
  {
    node: "negotiator",
    status: "completed",
    message: "Negotiation complete: $0.048/unit → $0.042/unit (-12.5%). Supplier accepted counter-offer.",
    timestamp: Date.now() + 9000,
  },
  {
    node: "hedge_engine",
    status: "running",
    message: "Locking upstream procurement at $0.031/unit. Arbitrage spread: 26.2%",
    timestamp: Date.now() + 10500,
  },
  {
    node: "hedge_engine",
    status: "completed",
    message: "HEDGE LOCKED ✓ — Buy: $0.031 | Sell: $0.042 | Spread: $5.50 net profit",
    timestamp: Date.now() + 12000,
  },
  {
    node: "regguard",
    status: "running",
    message: "Running export control screening: destination=India, product=passive components...",
    timestamp: Date.now() + 13000,
  },
  {
    node: "regguard",
    status: "completed",
    message: "CLEARED ✓ — No embargo match. EAR99 classification. Safe to proceed.",
    timestamp: Date.now() + 14500,
  },
  {
    node: "docuforge",
    status: "running",
    message: "Generating Proforma Invoice PI-2026-0417...",
    timestamp: Date.now() + 15500,
  },
  {
    node: "docuforge",
    status: "completed",
    message: "Proforma Invoice ready for buyer confirmation.",
    timestamp: Date.now() + 17000,
    data: { pi_ready: true },
  },
];

export function simulateSSEStream(
  onEvent: (event: StreamEvent) => void,
  onComplete: () => void,
): () => void {
  let index = 0;
  let cancelled = false;

  function next() {
    if (cancelled || index >= AGENT_MESSAGES.length) {
      if (!cancelled) onComplete();
      return;
    }
    const event = { ...AGENT_MESSAGES[index], timestamp: Date.now() };
    onEvent(event);
    index++;
    const delay = index < AGENT_MESSAGES.length ? 800 + Math.random() * 1200 : 0;
    setTimeout(next, delay);
  }

  setTimeout(next, 2500); // initial radar scan delay
  return () => { cancelled = true; };
}

export const MOCK_PROFORMA: ProformaInvoice = {
  pi_number: "PI-2026-0417",
  supplier_name: "Shenzhen YagTech Electronics Co., Ltd",
  buyer_name: "Rajesh Electronics Pvt Ltd",
  buyer_country: "India",
  product_description: "MLCC Ceramic Capacitor 100nF 50V 0805 (CLAW-ELEC-CAP-100NF50V)",
  quantity: 500,
  unit_price_usd: 0.042,
  total_usd: 21.0,
  incoterm: "CIF Mumbai",
  payment_terms: "T/T 30% deposit, 70% before shipment",
  validity_days: 7,
  created_at: new Date().toISOString(),
};

// ─── Merchant: Market Tick Simulation ────────────────────────

const TICKER_SYMBOLS = [
  { ticker_id: "CLAW-ELEC-CAP-100NF50V", symbol: "CAP-100NF", base: 0.042 },
  { ticker_id: "CLAW-ELEC-RES-10K0805", symbol: "RES-10K", base: 0.008 },
  { ticker_id: "CLAW-ELEC-IC-STM32F4", symbol: "IC-STM32", base: 4.85 },
  { ticker_id: "CLAW-ELEC-DIODE-1N4007", symbol: "DIODE-4007", base: 0.015 },
  { ticker_id: "CLAW-OPTO-LED-5MMWHT", symbol: "LED-5MM", base: 0.022 },
  { ticker_id: "CLAW-CONN-CONN-USB-C", symbol: "CONN-USBC", base: 0.18 },
  { ticker_id: "CLAW-ELEC-XTAL-16MHZ", symbol: "XTAL-16M", base: 0.35 },
  { ticker_id: "CLAW-ELEC-CAP-10UF25V", symbol: "CAP-10UF", base: 0.065 },
];

export function generateMarketTick(): MarketTick {
  const item = TICKER_SYMBOLS[Math.floor(Math.random() * TICKER_SYMBOLS.length)];
  const change = (Math.random() - 0.48) * item.base * 0.05;
  const price = Math.max(0.001, item.base + change);
  const changePct = ((price - item.base) / item.base) * 100;
  return {
    ticker_id: item.ticker_id,
    symbol: item.symbol,
    price: Number(price.toFixed(4)),
    prev_price: item.base,
    change_pct: Number(changePct.toFixed(2)),
    volume: Math.floor(1000 + Math.random() * 50000),
    timestamp: Date.now(),
  };
}

export function startMarketTickStream(
  onTick: (tick: MarketTick) => void,
): () => void {
  let active = true;
  function emit() {
    if (!active) return;
    onTick(generateMarketTick());
    setTimeout(emit, 300 + Math.random() * 700);
  }
  emit();
  return () => { active = false; };
}

// ─── Merchant: Event Feed Simulation ─────────────────────────

const EVENT_TEMPLATES: Omit<MarketEvent, "timestamp" | "event_id">[] = [
  { event_type: "hedge_locked", ticker_id: "CLAW-ELEC-CAP-100NF50V", data: { spread_pct: 26.2, amount_usd: 5.5 } },
  { event_type: "price_update", ticker_id: "CLAW-ELEC-RES-10K0805", data: { old: 0.008, new: 0.0082 } },
  { event_type: "reg_denied", ticker_id: "CLAW-ELEC-IC-STM32F4", data: { reason: "EAR controlled — destination: Iran", risk: "CRITICAL" } },
  { event_type: "negotiation_update", ticker_id: "CLAW-ELEC-CAP-10UF25V", data: { round: 2, action: "buyer_counter", delta_pct: -8.5 } },
  { event_type: "hedge_locked", ticker_id: "CLAW-OPTO-LED-5MMWHT", data: { spread_pct: 18.7, amount_usd: 12.3 } },
  { event_type: "document_generated", ticker_id: "CLAW-CONN-CONN-USB-C", data: { doc_type: "PI", pi_number: "PI-2026-0418" } },
  { event_type: "inventory_alert", ticker_id: "CLAW-ELEC-XTAL-16MHZ", data: { stock_qty: 45, threshold: 100 } },
  { event_type: "fx_tick", ticker_id: "USD/CNY", data: { rate: 7.2534, change: -0.0012 } },
  { event_type: "volatility_spike", ticker_id: "CLAW-ELEC-IC-STM32F4", data: { volatility: 0.15, trigger: "supply_shortage" } },
  { event_type: "reg_denied", ticker_id: "CLAW-ELEC-DIODE-1N4007", data: { reason: "Dual-use concern — destination: Russia", risk: "HIGH" } },
  { event_type: "hedge_locked", ticker_id: "CLAW-ELEC-RES-10K0805", data: { spread_pct: 31.0, amount_usd: 8.9 } },
  { event_type: "pending_review", ticker_id: "CLAW-ELEC-CAP-100NF50V", data: { margin_pct: 4.2, trade_id: "TRD-7821" } },
];

let eventCounter = 0;

export function generateMarketEvent(): MarketEvent {
  const template = EVENT_TEMPLATES[Math.floor(Math.random() * EVENT_TEMPLATES.length)];
  eventCounter++;
  return {
    ...template,
    timestamp: Date.now(),
    event_id: `evt-${eventCounter.toString().padStart(6, "0")}`,
  };
}

export function startEventStream(
  onEvent: (event: MarketEvent) => void,
): () => void {
  let active = true;
  function emit() {
    if (!active) return;
    onEvent(generateMarketEvent());
    setTimeout(emit, 1500 + Math.random() * 3000);
  }
  setTimeout(emit, 500);
  return () => { active = false; };
}

// ─── Merchant: HITL Pending Reviews ──────────────────────────

const REVIEW_POOL: Omit<PendingReview, "timestamp">[] = [
  {
    trade_id: "TRD-7821",
    buyer_name: "Rajesh Electronics",
    buyer_country: "India",
    product: "MLCC Capacitor 100nF 50V",
    quantity: 500,
    quoted_price_usd: 0.042,
    profit_margin_pct: 4.2,
    risk_score: 35,
    ticker_id: "CLAW-ELEC-CAP-100NF50V",
  },
  {
    trade_id: "TRD-7834",
    buyer_name: "TechParts GmbH",
    buyer_country: "Germany",
    product: "Crystal Oscillator 16MHz",
    quantity: 1000,
    quoted_price_usd: 0.38,
    profit_margin_pct: 3.8,
    risk_score: 22,
    ticker_id: "CLAW-ELEC-XTAL-16MHZ",
  },
  {
    trade_id: "TRD-7856",
    buyer_name: "SaoPaulo Components",
    buyer_country: "Brazil",
    product: "USB-C Connector",
    quantity: 2000,
    quoted_price_usd: 0.19,
    profit_margin_pct: 4.8,
    risk_score: 41,
    ticker_id: "CLAW-CONN-CONN-USB-C",
  },
];

export function generatePendingReview(): PendingReview {
  const item = REVIEW_POOL[Math.floor(Math.random() * REVIEW_POOL.length)];
  return { ...item, timestamp: Date.now() };
}

export function startReviewStream(
  onReview: (review: PendingReview) => void,
): () => void {
  let active = true;
  function emit() {
    if (!active) return;
    onReview(generatePendingReview());
    setTimeout(emit, 8000 + Math.random() * 12000);
  }
  setTimeout(emit, 3000);
  return () => { active = false; };
}

// ─── God Mode: KPIs ─────────────────────────────────────────

export const MOCK_KPIS: DashboardKPIs = {
  total_inquiries: 1247,
  hedge_success: 892,
  hedge_success_rate: 71.5,
  regguard_blocks: 43,
  block_types: { embargo: 18, dual_use: 12, sanctions: 8, other: 5 },
  inquiries_trend: [82, 95, 78, 110, 103, 125, 98, 134, 112, 145, 128, 137],
};

// ─── God Mode: Trade Routes ─────────────────────────────────

export const MOCK_TRADE_ROUTES: TradeRoute[] = [
  { id: "r1", origin: { lat: 22.54, lng: 114.06, label: "Shenzhen" }, destination: { lat: 19.07, lng: 72.87, label: "Mumbai" }, status: "success", amount_usd: 21.0, ticker_id: "CLAW-ELEC-CAP-100NF50V", timestamp: Date.now() },
  { id: "r2", origin: { lat: 23.02, lng: 113.75, label: "Dongguan" }, destination: { lat: 52.52, lng: 13.40, label: "Berlin" }, status: "success", amount_usd: 380.0, ticker_id: "CLAW-ELEC-XTAL-16MHZ", timestamp: Date.now() - 60000 },
  { id: "r3", origin: { lat: 31.23, lng: 121.47, label: "Shanghai" }, destination: { lat: -23.55, lng: -46.63, label: "São Paulo" }, status: "success", amount_usd: 456.0, ticker_id: "CLAW-CONN-CONN-USB-C", timestamp: Date.now() - 120000 },
  { id: "r4", origin: { lat: 22.54, lng: 114.06, label: "Shenzhen" }, destination: { lat: 35.68, lng: 139.69, label: "Tokyo" }, status: "success", amount_usd: 1250.0, ticker_id: "CLAW-ELEC-IC-STM32F4", timestamp: Date.now() - 180000 },
  { id: "r5", origin: { lat: 30.57, lng: 104.06, label: "Chengdu" }, destination: { lat: 35.69, lng: 51.39, label: "Tehran" }, status: "blocked", amount_usd: 890.0, ticker_id: "CLAW-ELEC-IC-STM32F4", timestamp: Date.now() - 240000 },
  { id: "r6", origin: { lat: 22.54, lng: 114.06, label: "Shenzhen" }, destination: { lat: 40.71, lng: -74.01, label: "New York" }, status: "success", amount_usd: 2100.0, ticker_id: "CLAW-ELEC-RES-10K0805", timestamp: Date.now() - 300000 },
  { id: "r7", origin: { lat: 23.13, lng: 113.26, label: "Guangzhou" }, destination: { lat: 55.75, lng: 37.62, label: "Moscow" }, status: "blocked", amount_usd: 670.0, ticker_id: "CLAW-ELEC-DIODE-1N4007", timestamp: Date.now() - 360000 },
  { id: "r8", origin: { lat: 22.54, lng: 114.06, label: "Shenzhen" }, destination: { lat: 1.35, lng: 103.82, label: "Singapore" }, status: "success", amount_usd: 540.0, ticker_id: "CLAW-OPTO-LED-5MMWHT", timestamp: Date.now() - 420000 },
];

// ─── God Mode: Audit Log ────────────────────────────────────

const LOG_TEMPLATES: Omit<AuditLogEntry, "id" | "timestamp">[] = [
  { level: "INFO", source_module: "workflow_graph", message: "Session TRD-7821 completed in 4.2s — all nodes passed", encrypted: false },
  { level: "INFO", source_module: "hedge_engine", message: "Procurement locked: CLAW-ELEC-CAP-100NF50V @ $0.031/unit from upstream supplier #3", encrypted: false },
  { level: "WARN", source_module: "regguard", message: "Near-miss: destination=Turkey, product=IC-STM32 — cleared with manual review flag", encrypted: false },
  { level: "ERROR", source_module: "supply_miner", message: "RPA scrape timeout on alibaba.com — retrying with stealth proxy rotation", encrypted: false },
  { level: "CRITICAL", source_module: "regguard", message: "BLOCKED: Export to Iran — CLAW-ELEC-IC-STM32F4 matches EAR Category 3A001", encrypted: true },
  { level: "INFO", source_module: "docuforge", message: "Generated PI-2026-0417 — SHA256: a3f8c2...9d1e", encrypted: false },
  { level: "INFO", source_module: "ticker_plant", message: "Ticker registered: CLAW-ELEC-CAP-10UF25V ← capacitor / 10uF 25V 0805", encrypted: false },
  { level: "WARN", source_module: "negotiator", message: "Round 3 timeout — buyer did not respond within 300s, auto-expiring offer", encrypted: false },
  { level: "INFO", source_module: "ledger", message: "Transaction settled: TXN-2026-04170892 | $21.00 | fee: $0.21 | sig: verified", encrypted: false },
  { level: "ERROR", source_module: "heartbeat", message: "RPA Worker #2 missed 3 consecutive heartbeats — marking as DEAD", encrypted: false },
  { level: "INFO", source_module: "matching_graph", message: "Match score 0.92 for demand D-4417 ↔ SKU S-8823 (Yageo MLCC)", encrypted: false },
  { level: "CRITICAL", source_module: "security", message: "Hardware token mismatch detected — request rejected from unknown machine", encrypted: true },
];

let logCounter = 0;

export function generateAuditLog(): AuditLogEntry {
  const template = LOG_TEMPLATES[Math.floor(Math.random() * LOG_TEMPLATES.length)];
  logCounter++;
  return {
    ...template,
    id: `log-${logCounter.toString().padStart(6, "0")}`,
    timestamp: new Date().toISOString(),
  };
}

export function startAuditLogStream(
  onLog: (entry: AuditLogEntry) => void,
): () => void {
  let active = true;
  function emit() {
    if (!active) return;
    onLog(generateAuditLog());
    setTimeout(emit, 800 + Math.random() * 2000);
  }
  emit();
  return () => { active = false; };
}
