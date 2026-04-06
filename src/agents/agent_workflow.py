"""
agents.agent_workflow — 供应链撮合风险防御子工作流
────────────────────────────────────────────────
在 RAG/供应链检索之后、谈判之前执行：
  1. 价格波动熔断：标价 vs 历史均价偏差 >30% → PriceVolatilityMonitor 二次确认
  2. 库存防错：Inventory Agent 校验后可用量 <100 → 报价附录强制声明

供 matching_graph 的 risk_defense 节点、控制面板与 run_business 复用。
"""

from __future__ import annotations

import hashlib
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)


def _sku_hash_ratio(sku_id: str, span: float = 1.0) -> float:
    """确定性 [0, span) 伪随机，便于回归与演示可复现。"""
    h = int(hashlib.md5(sku_id.encode()).hexdigest()[:8], 16)
    return (h % 10_000) / 10_000.0 * span


class PriceVolatilityMonitor:
    """价格异常波动监控（mock：历史均价 + 波动率曲面，触发二次确认）"""

    @staticmethod
    def historical_avg_price(sku_id: str, target_price_rmb: float) -> float:
        """Mock 历史成交均价：与当前标价解耦，便于演示 >30% 偏离场景。"""
        if target_price_rmb <= 0:
            return 0.01
        # 0.55 ~ 1.45 倍标价，偏离分布足够宽
        mult = 0.55 + _sku_hash_ratio(sku_id, 0.90)
        return round(target_price_rmb * mult, 6)

    @staticmethod
    def secondary_confirm(
        sku_id: str,
        target_price_rmb: float,
        historical_avg_rmb: float,
    ) -> dict[str, Any]:
        """二次确认（mock）：模拟风控引擎复核波动率、是否允许带风险继续报价。"""
        sigma = round(0.12 + _sku_hash_ratio(sku_id, 0.35), 4)
        band_hi = historical_avg_rmb * (1 + sigma * 2.2)
        band_lo = historical_avg_rmb * (1 - sigma * 2.2)
        in_band = band_lo <= target_price_rmb <= band_hi
        return {
            "sku_id": sku_id,
            "confirmed": True,
            "mock_volatility_sigma": sigma,
            "price_band_rmb": (round(band_lo, 6), round(band_hi, 6)),
            "within_statistical_band": in_band,
            "escalation": "人工复核队列(模拟)" if not in_band else None,
            "note": "PriceVolatilityMonitor：已完成二次波动确认（mock）",
        }


class InventoryAgent:
    """RAG 后的库存核对智能体（mock：ERP 同步抖动，可低于上架库存）"""

    LOW_STOCK_THRESHOLD = 100
    DISCLAIMER = "库存紧缺，请限期确认"

    @classmethod
    def verify_after_rag(cls, sku_id: str, listed_qty: int) -> dict[str, Any]:
        """模拟仓配回写：在列表库存基础上轻微调整，作为「Agent 返回值」。"""
        try:
            q = int(listed_qty)
        except (TypeError, ValueError):
            q = 0
        jitter = int(((_sku_hash_ratio(sku_id, 14.0)) - 3))  # 约 -3..+10
        verified = max(0, q + jitter)
        return {
            "sku_id": sku_id,
            "listed_qty": q,
            "verified_qty": verified,
            "source": "mock_erp_sync",
        }


def price_deviation_pct(target: float, historical: float) -> float:
    if historical is None or historical <= 0 or target is None:
        return 0.0
    return abs(target - historical) / historical * 100.0


def apply_risk_defense_to_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """就地增强候选 SKU：熔断标记、波动监控结果、库存附录。返回同一列表便于链式调用。"""
    monitor = PriceVolatilityMonitor()
    threshold_pct = 30.0

    for c in candidates:
        sku_id = str(c.get("sku_id", ""))
        target = float(c.get("unit_price_rmb", 0) or 0)
        hist = monitor.historical_avg_price(sku_id, target)
        c["historical_avg_price_rmb"] = hist
        dev = price_deviation_pct(target, hist)
        c["price_deviation_vs_hist_pct"] = round(dev, 2)

        c["abnormal_quote_risk"] = False
        c["volatility_monitor_result"] = None

        if dev > threshold_pct:
            c["abnormal_quote_risk"] = True
            c["volatility_monitor_result"] = monitor.secondary_confirm(sku_id, target, hist)
            logger.info(
                "价格波动熔断: sku=%s target=%s hist=%s dev=%.1f%% → 已调用 PriceVolatilityMonitor",
                sku_id[:8], target, hist, dev,
            )

        inv = InventoryAgent.verify_after_rag(sku_id, c.get("stock_qty", 0))
        c["inventory_agent"] = inv
        vq = int(inv.get("verified_qty", 0))
        c["inventory_verified_qty"] = vq

        appendix = ""
        if vq < InventoryAgent.LOW_STOCK_THRESHOLD:
            appendix = f"【{InventoryAgent.DISCLAIMER}】"
        c["quote_offer_appendix"] = appendix
        c["inventory_low_stock"] = bool(appendix)

    return candidates
