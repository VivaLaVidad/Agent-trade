"""
modules.supply_chain.tiered_quote — 阶梯报价引擎
─────────────────────────────────────────────────
职责：
  1. 基于候选 SKU + 需求参数，生成多档阶梯报价（Option A/B/C）
  2. 每档包含不同 MOQ、单价、预付款比例、交期
  3. 自动计算各档落地成本（含汇率 + 运费）
  4. 输出结构化报价看板，供前端/小程序直接展示

阶梯逻辑：
  - Option A: 买家需求量 → 标准单价，无预付款要求
  - Option B: 5x 需求量 → 折扣 10-15%，无预付款
  - Option C: 10x+ 需求量 → 折扣 18-25%，预付款 30%
"""

from __future__ import annotations

import math
from typing import Any

from modules.supply_chain.fx_service import FxRateService
from core.logger import get_logger

logger = get_logger(__name__)


class TieredQuoteEngine:
    """阶梯报价生成引擎

    根据候选 SKU 的基础价格和 MOQ，自动生成 3 档阶梯报价，
    每档包含完整的落地成本计算。
    """

    # 阶梯配置：(倍率, 折扣率, 预付款比例, 标签)
    _TIERS: list[tuple[float, float, float, str]] = [
        (1.0,  0.00, 0.00, "A"),   # 标准档：原价，无预付
        (5.0,  0.12, 0.00, "B"),   # 批量档：12% 折扣，无预付
        (10.0, 0.22, 0.30, "C"),   # 大单档：22% 折扣，30% 预付
    ]

    def __init__(self) -> None:
        self._fx = FxRateService()

    def generate_tiers(
        self,
        candidate: dict[str, Any],
        demand: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """为单个候选 SKU 生成阶梯报价

        Parameters
        ----------
        candidate : dict
            候选 SKU 信息（含 unit_price_rmb, moq, sku_name 等）
        demand : dict
            结构化需求（含 quantity, destination, budget_usd 等）

        Returns
        -------
        list[dict]
            3 档阶梯报价，每档包含完整定价和落地成本
        """
        base_price_rmb: float = float(candidate.get("unit_price_rmb", 0))
        base_moq: int = int(candidate.get("moq", 100))
        buyer_qty: int = int(demand.get("quantity", 0))
        destination: str = demand.get("destination", "")
        shipping_term: str = demand.get("trade_term", "FOB")
        if shipping_term == "unknown":
            shipping_term = "FOB"

        sku_name: str = candidate.get("sku_name", "")
        supplier_name: str = candidate.get("supplier_name", "")

        tiers: list[dict[str, Any]] = []

        for multiplier, discount, prepay_pct, label in self._TIERS:
            tier_qty = max(
                int(math.ceil(buyer_qty * multiplier)),
                base_moq if multiplier == 1.0 else int(base_moq * multiplier),
            )
            tier_price_rmb = round(base_price_rmb * (1 - discount), 4)

            landed = self._fx.calculate_landed_cost(
                tier_price_rmb, tier_qty, destination, shipping_term,
            )

            unit_price_usd = round(
                landed["total_usd"] / tier_qty if tier_qty > 0 else 0, 4,
            )

            prepay_usd = round(landed["landed_usd"] * prepay_pct, 2)

            tier = {
                "option": label,
                "sku_name": sku_name,
                "supplier_name": supplier_name,
                "sku_id": candidate.get("sku_id", ""),
                "quantity": tier_qty,
                "unit_price_rmb": tier_price_rmb,
                "unit_price_usd": unit_price_usd,
                "total_rmb": landed["total_rmb"],
                "total_usd": landed["total_usd"],
                "shipping_usd": landed["shipping_usd"],
                "landed_usd": landed["landed_usd"],
                "fx_rate": landed["fx_rate"],
                "shipping_term": shipping_term,
                "discount_pct": round(discount * 100, 1),
                "prepay_pct": round(prepay_pct * 100, 1),
                "prepay_usd": prepay_usd,
                "balance_usd": round(landed["landed_usd"] - prepay_usd, 2),
            }
            tiers.append(tier)

        logger.info(
            "阶梯报价生成: sku=%s tiers=%d base=¥%.4f",
            sku_name[:30], len(tiers), base_price_rmb,
        )
        return tiers

    def generate_multi_candidate_tiers(
        self,
        candidates: list[dict[str, Any]],
        demand: dict[str, Any],
        top_n: int = 3,
    ) -> list[dict[str, Any]]:
        """为多个候选 SKU 生成阶梯报价看板

        Parameters
        ----------
        candidates : list[dict]
            候选 SKU 列表（已按 match_score 排序）
        demand : dict
            结构化需求
        top_n : int
            取前 N 个候选生成阶梯报价

        Returns
        -------
        list[dict]
            每个候选的阶梯报价集合
        """
        results: list[dict[str, Any]] = []

        for cand in candidates[:top_n]:
            tiers = self.generate_tiers(cand, demand)
            results.append({
                "sku_id": cand.get("sku_id", ""),
                "sku_name": cand.get("sku_name", ""),
                "supplier_name": cand.get("supplier_name", ""),
                "match_score": cand.get("match_score", 0),
                "tiers": tiers,
                "abnormal_quote_risk": cand.get("abnormal_quote_risk", False),
                "inventory_verified_qty": cand.get("inventory_verified_qty"),
                "offer_disclaimer": (cand.get("quote_offer_appendix") or "").strip(),
            })

        logger.info(
            "多候选阶梯报价看板: candidates=%d tiers_per=%d",
            len(results), len(self._TIERS),
        )
        return results

    @staticmethod
    def format_tier_display(tier: dict[str, Any]) -> str:
        """格式化单档报价为可读文本（供日志/控制面板展示）"""
        return (
            f"Option {tier['option']}: "
            f"${tier['unit_price_usd']:.4f}/pc "
            f"(MOQ {tier['quantity']:,}) "
            f"{tier['shipping_term']} "
            f"落地 ${tier['landed_usd']:,.2f}"
            + (f" 预付 {tier['prepay_pct']:.0f}%" if tier['prepay_pct'] > 0 else "")
            + (f" 折扣 {tier['discount_pct']:.0f}%" if tier['discount_pct'] > 0 else "")
        )
