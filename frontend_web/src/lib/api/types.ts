// ─── Backend API Types ──────────────────────────────────────

export interface TradeRequest {
  session_id: string;
  intent_text: string;
  context: Record<string, unknown>;
}

export interface TradeResponse {
  session_id: string;
  status: "completed" | "failed" | "pending";
  result: Record<string, unknown>;
}

export interface HealthResponse {
  status: string;
  machine_bound: string;
}

// ─── SSE Stream Events ──────────────────────────────────────

export type AgentNode =
  | "intent_clarifier"
  | "supply_miner"
  | "hedge_engine"
  | "regguard"
  | "docuforge"
  | "negotiator"
  | "matching"
  | "ledger";

export interface StreamEvent {
  node: AgentNode;
  status: "running" | "completed" | "error";
  message: string;
  data?: Record<string, unknown>;
  timestamp: number;
}

// ─── Proforma Invoice ───────────────────────────────────────

export interface ProformaInvoice {
  pi_number: string;
  supplier_name: string;
  buyer_name: string;
  buyer_country: string;
  product_description: string;
  quantity: number;
  unit_price_usd: number;
  total_usd: number;
  incoterm: string;
  payment_terms: string;
  validity_days: number;
  created_at: string;
}

// ─── Market Data (Ticker Plant) ─────────────────────────────

export type EventType =
  | "price_update"
  | "fx_tick"
  | "volatility_spike"
  | "inventory_alert"
  | "negotiation_update"
  | "reg_denied"
  | "document_generated"
  | "hedge_locked"
  | "pending_review";

export interface MarketEvent {
  event_type: EventType;
  ticker_id: string;
  data: Record<string, unknown>;
  timestamp: number;
  event_id: string;
}

export interface MarketTick {
  ticker_id: string;
  symbol: string;
  price: number;
  prev_price: number;
  change_pct: number;
  volume: number;
  timestamp: number;
}

// ─── HITL Override ───────────────────────────────────────────

export interface PendingReview {
  trade_id: string;
  buyer_name: string;
  buyer_country: string;
  product: string;
  quantity: number;
  quoted_price_usd: number;
  profit_margin_pct: number;
  risk_score: number;
  ticker_id: string;
  timestamp: number;
}

export interface OverrideAction {
  action: "accept" | "reject";
}

// ─── God Mode KPIs ──────────────────────────────────────────

export interface DashboardKPIs {
  total_inquiries: number;
  hedge_success: number;
  hedge_success_rate: number;
  regguard_blocks: number;
  block_types: Record<string, number>;
  inquiries_trend: number[];
}

// ─── Trade Route (Globe) ────────────────────────────────────

export interface TradeRoute {
  id: string;
  origin: { lat: number; lng: number; label: string };
  destination: { lat: number; lng: number; label: string };
  status: "success" | "blocked";
  amount_usd: number;
  ticker_id: string;
  timestamp: number;
}

// ─── Audit Log ──────────────────────────────────────────────

export type LogLevel = "INFO" | "WARN" | "ERROR" | "CRITICAL" | "DEBUG";

export interface AuditLogEntry {
  id: string;
  timestamp: string;
  level: LogLevel;
  source_module: string;
  message: string;
  encrypted: boolean;
}
