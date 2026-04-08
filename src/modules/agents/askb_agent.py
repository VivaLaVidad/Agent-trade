"""
modules.agents.askb_agent — Bloomberg ASKB (Agentic Trader Copilot)
═══════════════════════════════════════════════════════════════════
对标 Bloomberg 2026 ASKB 智能体，为 /merchant 套利台操作员
提供自然语言穿透查询能力。

内置 Tools:
  1. query_inventory_profit — 查询 MockInventory 利润率预测
  2. query_market_bus — 从 MarketDataBus 读取最新底价事件
"""

from __future__ import annotations

import re
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)


class ASKBTraderCopilot:
    """Bloomberg ASKB 级交易员助手 — Tool-calling 轻量智能体

    解析自然语言指令，路由到内置 Tools，返回带数据支撑的决策建议。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Any] = {
            "query_inventory_profit": self._tool_inventory_profit,
            "query_market_bus": self._tool_market_bus,
        }

    async def process(self, query: str) -> dict[str, Any]:
        """处理自然语言查询，返回结构化决策建议

        Parameters
        ----------
        query : str
            用户自然语言指令，如 "分析 CLAW-CAP-MLCC-104 当前的外部套利空间"

        Returns
        -------
        dict[str, Any]
            包含 intent, tool_used, data, recommendation 的结构化响应
        """
        query_lower = query.lower().strip()

        # Intent detection via keyword matching (lightweight, no LLM dependency)
        intent, tool_name, tool_args = self._detect_intent(query_lower, query)

        if tool_name and tool_name in self._tools:
            logger.info("[ASKB] Intent=%s Tool=%s Args=%s", intent, tool_name, tool_args)
            data = await self._tools[tool_name](**tool_args)
            recommendation = self._generate_recommendation(intent, data)
            return {
                "intent": intent,
                "tool_used": tool_name,
                "data": data,
                "recommendation": recommendation,
                "status": "success",
            }

        return {
            "intent": "unknown",
            "tool_used": None,
            "data": {},
            "recommendation": f"无法识别指令意图。支持的查询类型：库存利润分析、市场底价查询。原始输入：{query[:100]}",
            "status": "unrecognized",
        }

    def _detect_intent(self, query_lower: str, query_raw: str) -> tuple[str, str | None, dict]:
        """Lightweight intent detection via keyword matching"""

        # Extract ticker/SKU patterns
        ticker_match = re.search(r"CLAW-[A-Z]+-[A-Z]+-\w+", query_raw, re.IGNORECASE)
        sku_match = re.search(r"SKU-[A-Z]+-\d+", query_raw, re.IGNORECASE)

        # Extract generic product keywords
        product_kw = ""
        if ticker_match:
            product_kw = ticker_match.group(0)
        elif sku_match:
            product_kw = sku_match.group(0)
        else:
            # Try to extract Chinese/English product names
            for kw in ["capacitor", "resistor", "mcu", "stm32", "esp32", "led",
                        "diode", "transistor", "connector", "inductor", "crystal",
                        "sensor", "mlcc", "100nf", "10k", "usb"]:
                if kw in query_lower:
                    product_kw = kw
                    break

        # Route to tools
        inventory_keywords = ["库存", "利润", "profit", "inventory", "margin", "成本", "cost", "本地"]
        market_keywords = ["市场", "底价", "market", "price", "tick", "bus", "套利", "arbitrage", "外部"]

        if any(k in query_lower for k in inventory_keywords):
            return "inventory_profit_analysis", "query_inventory_profit", {"sku": product_kw}

        if any(k in query_lower for k in market_keywords):
            return "market_price_query", "query_market_bus", {"ticker": product_kw}

        # Default: if we have a product keyword, try inventory first
        if product_kw:
            return "inventory_profit_analysis", "query_inventory_profit", {"sku": product_kw}

        return "unknown", None, {}

    async def _tool_inventory_profit(self, sku: str) -> dict[str, Any]:
        """Tool 1: 查询 MockInventory 并返回利润率预测"""
        from database.mock_inventory import get_mock_inventory

        inventory = get_mock_inventory()
        hits = inventory.query(sku_name=sku, qty=1)

        if not hits:
            return {
                "found": False,
                "sku_query": sku,
                "message": f"本地库存未找到匹配 '{sku}' 的 SKU",
                "items": [],
            }

        items = []
        for h in hits[:5]:
            items.append({
                "sku_id": h["sku_id"],
                "sku_name": h["sku_name"],
                "stock_qty": h["stock_qty"],
                "cost_price_usd": h["cost_price"],
                "suggested_sell_usd": h["suggested_sell_price"],
                "profit_margin_pct": h["profit_margin_pct"],
                "location": h["location"],
            })

        return {
            "found": True,
            "sku_query": sku,
            "match_count": len(items),
            "items": items,
            "best_margin_pct": max(i["profit_margin_pct"] for i in items),
        }

    async def _tool_market_bus(self, ticker: str) -> dict[str, Any]:
        """Tool 2: 从 MarketDataBus 读取最新底价事件"""
        from core.ticker_plant import get_market_bus, get_ticker_registry

        bus = get_market_bus()
        registry = get_ticker_registry()

        # Try to resolve ticker
        resolved = None
        if ticker.startswith("CLAW-"):
            events = bus.get_ticker_events(ticker, limit=5)
            resolved = ticker
        else:
            # Search registry
            results = registry.search(ticker, limit=3)
            if results:
                resolved = results[0].ticker_id
                events = bus.get_ticker_events(resolved, limit=5)
            else:
                events = []

        if not events:
            return {
                "found": False,
                "ticker_query": ticker,
                "resolved_ticker": resolved,
                "message": f"MarketDataBus 无 '{ticker}' 的近期事件（总线可能未启动或无数据）",
                "events": [],
            }

        event_list = []
        for e in events:
            event_list.append({
                "event_type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                "ticker_id": e.ticker_id,
                "payload": e.payload,
                "timestamp": str(e.timestamp),
            })

        return {
            "found": True,
            "ticker_query": ticker,
            "resolved_ticker": resolved,
            "event_count": len(event_list),
            "events": event_list,
        }

    @staticmethod
    def _generate_recommendation(intent: str, data: dict) -> str:
        """Generate human-readable recommendation from tool output"""
        if intent == "inventory_profit_analysis":
            if not data.get("found"):
                return f"本地库存无匹配项。建议通过 ScatterNode 向外部节点询价，或等待 Ghost Miner 动态寻价。"
            best = data.get("best_margin_pct", 0)
            count = data.get("match_count", 0)
            if best > 10:
                return f"发现 {count} 个匹配 SKU，最高利润率 {best:.1f}%。建议走 LOCAL_INVENTORY 路径直接撮合，预期利润空间充足。"
            elif best > 5:
                return f"发现 {count} 个匹配 SKU，最高利润率 {best:.1f}%。利润率刚过阈值，建议同时触发 ScatterNode 对比外部报价。"
            else:
                return f"发现 {count} 个匹配 SKU，但最高利润率仅 {best:.1f}%（低于 5% 阈值）。建议走 REMOTE_ARBITRAGE 路径寻找更优报价。"

        if intent == "market_price_query":
            if not data.get("found"):
                return "MarketDataBus 无近期事件。建议检查 Redis 连接状态或手动触发 Tick 更新。"
            count = data.get("event_count", 0)
            return f"获取到 {count} 条近期市场事件。请查看 events 字段中的详细 Tick 数据进行套利分析。"

        return "查询完成，请查看 data 字段获取详细信息。"


# Singleton
_askb_instance: ASKBTraderCopilot | None = None


def get_askb_copilot() -> ASKBTraderCopilot:
    global _askb_instance
    if _askb_instance is None:
        _askb_instance = ASKBTraderCopilot()
    return _askb_instance
