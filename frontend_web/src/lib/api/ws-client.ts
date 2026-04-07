type WSStatus = "connecting" | "connected" | "disconnected" | "error";
type MessageHandler<T> = (data: T) => void;
type StatusHandler = (status: WSStatus) => void;

/**
 * Derive a WebSocket URL from an HTTP base URL.
 * https:// → wss://, http:// → ws://
 */
export function deriveWsUrl(httpBase: string, path: string = "/ws"): string {
  const base = httpBase.replace(/\/+$/, "");
  const wsBase = base
    .replace(/^https:\/\//, "wss://")
    .replace(/^http:\/\//, "ws://");
  return `${wsBase}${path}`;
}

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8900";

/** Pre-built default WS URL derived from the API base environment variable */
export const DEFAULT_WS_URL = deriveWsUrl(API_BASE);

export class WSClient<T = unknown> {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: Set<MessageHandler<T>> = new Set();
  private statusHandlers: Set<StatusHandler> = new Set();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 20;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private _status: WSStatus = "disconnected";
  private shouldReconnect = true;

  constructor(url: string) {
    this.url = url;
  }

  get status(): WSStatus {
    return this._status;
  }

  connect(): void {
    this.shouldReconnect = true;
    this.setStatus("connecting");

    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.reconnectAttempts = 0;
        this.setStatus("connected");
        this.startHeartbeat();
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as T;
          this.handlers.forEach((h) => h(data));
        } catch {
          // non-JSON message (heartbeat pong, etc.)
        }
      };

      this.ws.onclose = () => {
        this.setStatus("disconnected");
        this.stopHeartbeat();
        if (this.shouldReconnect) this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        this.setStatus("error");
      };
    } catch {
      this.setStatus("error");
      if (this.shouldReconnect) this.scheduleReconnect();
    }
  }

  disconnect(): void {
    this.shouldReconnect = false;
    this.stopHeartbeat();
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
    this.setStatus("disconnected");
  }

  onMessage(handler: MessageHandler<T>): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  onStatus(handler: StatusHandler): () => void {
    this.statusHandlers.add(handler);
    return () => this.statusHandlers.delete(handler);
  }

  send(data: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  private setStatus(s: WSStatus): void {
    this._status = s;
    this.statusHandlers.forEach((h) => h(s));
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) return;
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30_000);
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  private startHeartbeat(): void {
    this.heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "ping" }));
      }
    }, 25_000);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }
}
