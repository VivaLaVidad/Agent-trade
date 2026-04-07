"""
bloomberg_tui.py — Project Claw Bloomberg-like 专业多面板控制台
═══════════════════════════════════════════════════════════════
对标 Bloomberg Terminal 的四面板布局 (Textual TUI):

  ┌─────────────────────────┬──────────────────────────┐
  │  Market Depth           │  Active Negotiations     │
  │  重点 Ticker 底价跳动    │  pending/counter_offer   │
  │  (Tick Stream)          │  A2A 谈判流              │
  ├─────────────────────────┼──────────────────────────┤
  │  Audit Trail            │  System Health           │
  │  脱敏加密日志滚动        │  gRPC / PG / CPU / MEM   │
  │  (ComplianceGateway)    │  连接状态                 │
  └─────────────────────────┴──────────────────────────┘

启动方式: python bloomberg_tui.py
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from dotenv import load_dotenv
load_dotenv()

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Static, DataTable, RichLog, Label
from textual.timer import Timer
from textual import work

from rich.text import Text

from core.ticker_plant import (
    EventType,
    MarketEvent,
    get_market_bus,
    get_ticker_registry,
)
from modules.supply_chain.tick_pricing import TickPricingEngine


# ═══════════════════════════════════════════════════════════════
#  Market Depth Panel — 重点 Ticker 底价跳动
# ═══════════════════════════════════════════════════════════════

class MarketDepthPanel(Static):
    """Top-Left: 实时 Ticker 底价跳动 (Tick Stream)"""

    DEFAULT_CSS = """
    MarketDepthPanel {
        border: solid #1a3d5c;
        background: #0a0e14;
        height: 1fr;
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("[bold #00e5cc]◆ MARKET DEPTH — Tick Stream[/]", id="md-title")
        yield DataTable(id="md-table")

    def on_mount(self) -> None:
        table = self.query_one("#md-table", DataTable)
        table.add_columns("Ticker", "Price ¥", "Δ%", "Vol7d", "Score", "Time")
        table.cursor_type = "row"
        table.zebra_stripes = True


class ActiveNegotiationsPanel(Static):
    """Top-Right: 当前活跃谈判流"""

    DEFAULT_CSS = """
    ActiveNegotiationsPanel {
        border: solid #1a3d5c;
        background: #0a0e14;
        height: 1fr;
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("[bold #ff9800]◆ ACTIVE NEGOTIATIONS[/]", id="neg-title")
        yield DataTable(id="neg-table")

    def on_mount(self) -> None:
        table = self.query_one("#neg-table", DataTable)
        table.add_columns("ID", "Ticker", "Status", "Round", "Offer $", "Time")
        table.cursor_type = "row"
        table.zebra_stripes = True


class AuditTrailPanel(Static):
    """Bottom-Left: 脱敏加密审计日志滚动"""

    DEFAULT_CSS = """
    AuditTrailPanel {
        border: solid #1a3d5c;
        background: #0a0e14;
        height: 1fr;
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("[bold #7cfc00]◆ AUDIT TRAIL — Encrypted Log[/]", id="audit-title")
        yield RichLog(id="audit-log", highlight=True, markup=True, max_lines=200)


class SystemHealthPanel(Static):
    """Bottom-Right: 系统健康状态"""

    DEFAULT_CSS = """
    SystemHealthPanel {
        border: solid #1a3d5c;
        background: #0a0e14;
        height: 1fr;
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("[bold #00bcd4]◆ SYSTEM HEALTH[/]", id="health-title")
        yield RichLog(id="health-log", highlight=True, markup=True, max_lines=100)


# ═══════════════════════════════════════════════════════════════
#  Bloomberg TUI App
# ═══════════════════════════════════════════════════════════════

# 模拟 Ticker 数据源
_DEMO_TICKERS = [
    ("capacitor", "100nF 50V 0805 MLCC Ceramic Capacitor"),
    ("capacitor", "100uF 25V SMD Electrolytic Capacitor"),
    ("resistor", "10K 0603 1% Thin Film Resistor"),
    ("led", "White LED SMD-2835 0.2W"),
    ("ic", "STM32F103 ARM Cortex-M3 MCU"),
    ("connector", "USB-C 24pin Female Connector"),
    ("regulator", "LM7805 5V LDO Voltage Regulator"),
    ("inductor", "10uH 2A SMD Power Inductor"),
]

_DEMO_NEGOTIATIONS = [
    {"id": "NEG-A001", "status": "pending", "round": 1, "offer": 150.00},
    {"id": "NEG-B002", "status": "counter_offer", "round": 3, "offer": 420.50},
    {"id": "NEG-C003", "status": "pending", "round": 1, "offer": 89.00},
    {"id": "NEG-D004", "status": "counter_offer", "round": 2, "offer": 1250.00},
]


class BloombergTUI(App):
    """Project Claw — Bloomberg Terminal 风格四面板控制台"""

    CSS = """
    Screen {
        background: #080c12;
    }
    Header {
        background: #0d1520;
        color: #00e5cc;
    }
    Footer {
        background: #0d1520;
    }
    #top-row {
        height: 1fr;
    }
    #bottom-row {
        height: 1fr;
    }
    Label {
        padding: 0 1;
        background: #0d1520;
        color: #c8d2dc;
    }
    DataTable {
        height: 1fr;
    }
    RichLog {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    """

    TITLE = "Project Claw — Bloomberg Terminal"
    SUB_TITLE = "Ticker Plant · Market Data Bus · Real-time Monitoring"

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("d", "toggle_dark", "Dark/Light"),
        ("r", "refresh", "Force Refresh"),
        ("s", "inject_spike", "Inject Spike"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._tick_engine = TickPricingEngine()
        self._registry = get_ticker_registry()
        self._bus = get_market_bus()
        self._ticker_cache: dict[str, dict] = {}
        self._tick_count = 0
        self._reg_denied_count = 0
        self._docuforge_count = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with Horizontal(id="top-row"):
                yield MarketDepthPanel()
                yield ActiveNegotiationsPanel()
            with Horizontal(id="bottom-row"):
                yield AuditTrailPanel()
                yield SystemHealthPanel()
        yield Footer()

    async def on_mount(self) -> None:
        # 启动 MarketDataBus
        await self._bus.start()

        # 订阅所有事件用于审计日志
        self._bus.subscribe("*", self._on_market_event)

        # 注册演示 Ticker
        for cat, name in _DEMO_TICKERS:
            ticker = self._registry.resolve(cat, name)
            self._ticker_cache[ticker.ticker_id] = {
                "ticker_id": ticker.ticker_id,
                "category": cat,
                "name": name,
                "last_price": round(random.uniform(0.1, 50.0), 4),
                "last_score": 100.0,
                "last_vol": 0.05,
                "last_delta": 0.0,
            }

        # 初始化谈判表
        self._init_negotiations()

        # 启动定时刷新
        self.set_interval(2.0, self._tick_refresh)
        self.set_interval(3.0, self._health_refresh)
        self.set_interval(5.0, self._negotiation_refresh)

        # 初始健康日志
        health_log = self.query_one("#health-log", RichLog)
        health_log.write("[bold #00bcd4]System initializing...[/]")
        health_log.write(f"[#8899aa]Python: {sys.executable}[/]")
        health_log.write(f"[#8899aa]Workspace: {os.getcwd()}[/]")
        health_log.write("[bold #00e5cc]MarketDataBus: ONLINE[/]")

    async def _on_market_event(self, event: MarketEvent) -> None:
        """全局事件处理 → 审计日志面板"""
        audit_log = self.query_one("#audit-log", RichLog)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        if event.event_type == EventType.PRICE_UPDATE:
            price = event.data.get("new_price_rmb", 0)
            audit_log.write(
                f"[#8899aa]{ts}[/] [#00e5cc]PRICE[/] "
                f"ticker={event.ticker_id[:28]} "
                f"price=¥{price:.4f} "
                f"sig=[#667788]***redacted***[/]"
            )
        elif event.event_type == EventType.VOLATILITY_SPIKE:
            vol = event.data.get("volatility_7d", 0)
            severity = event.data.get("severity", "medium")
            color = "#ff5555" if severity == "high" else "#ff9800"
            audit_log.write(
                f"[#8899aa]{ts}[/] [{color}]SPIKE[/] "
                f"ticker={event.ticker_id[:28]} "
                f"vol={vol:.4f} severity={severity} "
                f"[{color}]⚠ INTERRUPT TRIGGERED[/]"
            )
        elif event.event_type == EventType.FX_TICK:
            rate = event.data.get("fx_rate_mid", 7.25)
            audit_log.write(
                f"[#8899aa]{ts}[/] [#7b8cff]FX[/] "
                f"USD/CNY={rate:.4f} "
                f"email=[#667788]b***@***.com[/] "
                f"sig=[#667788]OK[/]"
            )
        elif event.event_type == EventType.REG_DENIED:
            dest = event.data.get("destination", "?")
            rules = event.data.get("matched_rules", [])
            self._reg_denied_count += 1
            audit_log.write(
                f"[#8899aa]{ts}[/] [bold #ff5555][REG-DENIED][/] "
                f"ticker={event.ticker_id[:28]} dest={dest} "
                f"rules={len(rules)} "
                f"[bold #ff5555]⛔ EXPORT CONTROL BLOCK[/]"
            )
        elif event.event_type == EventType.DOCUMENT_GENERATED:
            doc_hash = event.data.get("document_hash", "")[:16]
            po = event.data.get("po_number", "?")
            self._docuforge_count += 1
            audit_log.write(
                f"[#8899aa]{ts}[/] [bold #4488ff][DOCUFORGE][/] "
                f"PO={po} hash={doc_hash}… "
                f"[#4488ff]PDF generated ✓[/]"
            )
        elif event.event_type == EventType.NEGOTIATION_UPDATE:
            action = event.data.get("action", "")
            if action == "REG_DENIED":
                dest = event.data.get("destination", "?")
                self._reg_denied_count += 1
                audit_log.write(
                    f"[#8899aa]{ts}[/] [bold #ff5555][REG-DENIED][/] "
                    f"ticker={event.ticker_id[:28]} dest={dest} "
                    f"[bold #ff5555]⛔ EXPORT CONTROL BLOCK[/]"
                )

    def _tick_refresh(self) -> None:
        """定时刷新 Market Depth 面板"""
        self._tick_count += 1
        table = self.query_one("#md-table", DataTable)
        table.clear()

        for tid, cache in self._ticker_cache.items():
            # 模拟 Tick 定价
            base_price = cache["last_price"] * (1 + random.uniform(-0.03, 0.03))
            result = self._tick_engine.compute_tick(
                base_price_rmb=base_price,
                stock_qty=random.randint(500, 50000),
                moq=random.randint(50, 500),
                demand_qty=random.randint(100, 5000),
                ticker_id=tid,
            )

            new_price = result["adjusted_price_rmb"]
            delta_pct = result["tick_adjustment_pct"]
            vol_7d = result["market_snapshot"]["volatility"]["volatility_7d"]
            score = result["tick_score"]
            is_spike = result.get("is_volatility_spike", False)

            cache["last_price"] = new_price
            cache["last_score"] = score
            cache["last_vol"] = vol_7d
            cache["last_delta"] = delta_pct

            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

            # 颜色编码
            delta_color = "red" if delta_pct > 0 else "green" if delta_pct < 0 else "white"
            spike_marker = " ⚡" if is_spike else ""

            table.add_row(
                Text(tid[:24] + spike_marker, style="bold #00e5cc" if not is_spike else "bold #ff5555"),
                Text(f"¥{new_price:.4f}", style="#c8d2dc"),
                Text(f"{delta_pct:+.2f}%", style=delta_color),
                Text(f"{vol_7d:.4f}", style="#ff9800" if vol_7d > 0.1 else "#8899aa"),
                Text(f"{score:.1f}", style="#7cfc00" if score > 110 else "#c8d2dc"),
                Text(ts, style="#667788"),
            )

    def _init_negotiations(self) -> None:
        """初始化谈判面板"""
        table = self.query_one("#neg-table", DataTable)
        tickers = list(self._ticker_cache.keys())
        for i, neg in enumerate(_DEMO_NEGOTIATIONS):
            tid = tickers[i % len(tickers)] if tickers else "N/A"
            neg["ticker_id"] = tid
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            status_style = "#ff9800" if neg["status"] == "counter_offer" else "#00e5cc"
            table.add_row(
                neg["id"],
                Text(tid[:20], style="#00e5cc"),
                Text(neg["status"], style=status_style),
                str(neg["round"]),
                f"${neg['offer']:.2f}",
                ts,
            )

    def _negotiation_refresh(self) -> None:
        """定时刷新谈判面板"""
        table = self.query_one("#neg-table", DataTable)
        table.clear()
        tickers = list(self._ticker_cache.keys())

        for i, neg in enumerate(_DEMO_NEGOTIATIONS):
            # 模拟状态变化
            if random.random() < 0.3:
                neg["round"] = min(neg["round"] + 1, 10)
                neg["status"] = random.choice(["pending", "counter_offer", "counter_offer"])
                neg["offer"] *= (1 + random.uniform(-0.05, 0.05))

            tid = neg.get("ticker_id", tickers[i % len(tickers)] if tickers else "N/A")
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            status_style = "#ff9800" if neg["status"] == "counter_offer" else "#00e5cc"

            table.add_row(
                neg["id"],
                Text(tid[:20], style="#00e5cc"),
                Text(neg["status"], style=status_style),
                str(neg["round"]),
                f"${neg['offer']:.2f}",
                ts,
            )

    def _health_refresh(self) -> None:
        """定时刷新系统健康面板"""
        import psutil

        health_log = self.query_one("#health-log", RichLog)
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        cpu = psutil.cpu_percent(interval=0)
        mem = psutil.virtual_memory()
        mem_mb = round(mem.used / (1024 * 1024), 1)

        cpu_color = "#ff5555" if cpu > 80 else "#ff9800" if cpu > 50 else "#00e5cc"
        mem_color = "#ff5555" if mem.percent > 85 else "#ff9800" if mem.percent > 60 else "#00e5cc"

        health_log.write(
            f"[#667788]{ts}[/] "
            f"CPU=[{cpu_color}]{cpu:.1f}%[/] "
            f"MEM=[{mem_color}]{mem_mb:.0f}MB ({mem.percent:.0f}%)[/] "
            f"Bus=[#00e5cc]{'REDIS' if self._bus._redis else 'LOCAL'}[/] "
            f"Tickers=[#7cfc00]{len(self._ticker_cache)}[/] "
            f"Events=[#7cfc00]{len(self._bus.get_recent_events())}[/]"
        )

        # gRPC 状态 (模拟)
        grpc_status = random.choice(["CONNECTED", "CONNECTED", "CONNECTED", "RECONNECTING"])
        grpc_color = "#00e5cc" if grpc_status == "CONNECTED" else "#ff9800"
        reg_color = "#ff5555" if self._reg_denied_count > 0 else "#00e5cc"
        health_log.write(
            f"[#667788]{ts}[/] "
            f"gRPC=[{grpc_color}]{grpc_status}[/] "
            f"PG_pool=[#00e5cc]active[/] "
            f"PG_wait_queue=[#8899aa]{random.randint(0, 3)}[/] "
            f"RegGuard=[{reg_color}]{self._reg_denied_count} denied[/] "
            f"DocuForge=[#4488ff]{self._docuforge_count} docs[/]"
        )

    def action_toggle_dark(self) -> None:
        self.dark = not self.dark

    def action_refresh(self) -> None:
        """强制刷新所有面板"""
        self._tick_refresh()
        self._negotiation_refresh()
        self._health_refresh()

    async def action_inject_spike(self) -> None:
        """手动注入波动率突变事件 (测试用)"""
        tickers = list(self._ticker_cache.keys())
        if not tickers:
            return
        target = random.choice(tickers)
        spike_event = MarketEvent(
            event_type=EventType.VOLATILITY_SPIKE,
            ticker_id=target,
            data={
                "volatility_7d": round(random.uniform(0.15, 0.25), 4),
                "fx_drift": round(random.uniform(-1.0, 1.0), 4),
                "fx_rate_mid": round(7.25 + random.uniform(-0.3, 0.3), 4),
                "threshold": 0.12,
                "severity": "high",
            },
        )
        await self._bus.publish(spike_event)

        audit_log = self.query_one("#audit-log", RichLog)
        audit_log.write(
            f"[bold #ff5555]>>> MANUAL SPIKE INJECTION: {target} <<<[/]"
        )

    async def on_unmount(self) -> None:
        await self._bus.stop()


def main() -> None:
    app = BloombergTUI()
    app.run()


if __name__ == "__main__":
    main()
