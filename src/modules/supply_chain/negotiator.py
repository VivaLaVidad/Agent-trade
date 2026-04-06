"""
modules.supply_chain.negotiator — 贸易谈判决策智能体（含阶梯报价）
──────────────────────────────────────────────────────────────────
职责：
  1. MOQ 校验：数量不足 → 拼单建议 (SuggestBundling)
  2. 认证校验：资质不符 → 推荐平替 (RecommendAlternative)
  3. 预算校验：超预算 → 替代方案 / 部分履约
  4. 贸易术语选择：基于目的地自动推荐 FOB / CIF
  5. 阶梯报价生成：为 approved 候选生成 Option A/B/C 多档报价
  6. 输出最终谈判结果 + 阶梯报价看板 + 备选方案列表
"""

from __future__ import annotations

from typing import Any

from modules.supply_chain.fx_service import FxRateService
from modules.supply_chain.tiered_quote import TieredQuoteEngine
from core.logger import get_logger

logger = get_logger(__name__)


class NegotiatorAgent:
    """贸易条款谈判决策引擎（含阶梯报价）

    实现完整的决策树：MOQ → 认证 → 预算 → 贸易术语 → 阶梯报价
    每个失败分支都有回退策略（拼单/平替/部分履约）。
    通过的候选自动生成 3 档阶梯报价看板。
    """

    def __init__(self) -> None:
        self._fx = FxRateService()
        self._tiered = TieredQuoteEngine()

    async def execute(
        self,
        ctx: Any,
        demand: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """对每个候选 SKU 执行谈判决策树 + 阶梯报价

        Returns
        -------
        dict
            {
              "best_match": dict | None,
              "all_approved": list[dict],
              "alternatives": list[dict],
              "bundling_suggestions": list[dict],
              "tiered_quotes": list[dict],
              "negotiation_log": list[str],
            }
        """
        quantity: int = int(demand.get("quantity", 0))
        budget_usd: float = float(demand.get("budget_usd", 0))
        certs_req: list = demand.get("certs_required", [])
        destination: str = demand.get("destination", "")

        log: list[str] = []
        approved: list[dict] = []
        alternatives: list[dict] = []
        bundling: list[dict] = []

        for cand in candidates:
            result = self._evaluate_candidate(
                cand, quantity, budget_usd, certs_req, destination, log,
            )

            if result["status"] == "approved":
                approved.append(result)
            elif result["status"] == "alternative":
                alternatives.append(result)
            elif result["status"] == "bundling":
                bundling.append(result)

        best = approved[0] if approved else (alternatives[0] if alternatives else None)

        # ── 为通过的候选生成阶梯报价看板 ──
        tiered_quotes: list[dict[str, Any]] = []
        quote_candidates = approved if approved else alternatives[:2]
        if quote_candidates:
            # 从原始 candidates 中找到对应的完整数据
            approved_ids = {r.get("sku_id") for r in quote_candidates}
            full_candidates = [c for c in candidates if c.get("sku_id") in approved_ids]
            tiered_quotes = self._tiered.generate_multi_candidate_tiers(
                full_candidates, demand, top_n=3,
            )
            for tq in tiered_quotes:
                for tier in tq.get("tiers", []):
                    log.append(
                        f"[TIER] {TieredQuoteEngine.format_tier_display(tier)}"
                    )

        logger.info(
            "谈判完成: approved=%d alternatives=%d bundling=%d tiered=%d",
            len(approved), len(alternatives), len(bundling), len(tiered_quotes),
        )
        return {
            "best_match": best,
            "all_approved": approved,
            "alternatives": alternatives,
            "bundling_suggestions": bundling,
            "tiered_quotes": tiered_quotes,
            "negotiation_log": log,
        }

    def _evaluate_candidate(
        self,
        cand: dict,
        quantity: int,
        budget_usd: float,
        certs_req: list,
        destination: str,
        log: list[str],
    ) -> dict[str, Any]:
        sku_name = cand.get("sku_name", "?")
        sku_id = cand.get("sku_id", "")
        moq = cand.get("moq", 0)
        cand_certs = cand.get("certifications", [])
        unit_price = cand.get("unit_price_rmb", 0)
        supplier_name = cand.get("supplier_name", "?")

        shipping_term = self._select_shipping_term(destination)

        landed = self._fx.calculate_landed_cost(
            unit_price, quantity, destination, shipping_term,
        )

        offer_appendix = (cand.get("quote_offer_appendix") or "").strip()
        if cand.get("abnormal_quote_risk"):
            vm = cand.get("volatility_monitor_result") or {}
            log.append(
                f"[VOLATILITY] {sku_name}: 异常报价风险 — 已调用 PriceVolatilityMonitor 二次确认 "
                f"({vm.get('note', 'mock')})",
            )

        result_base = {
            "sku_id": sku_id,
            "sku_name": sku_name,
            "supplier_name": supplier_name,
            "match_score": cand.get("match_score", 0),
            "shipping_term": shipping_term,
            "offer_disclaimer": offer_appendix,
            "abnormal_quote_risk": bool(cand.get("abnormal_quote_risk")),
            "inventory_verified_qty": cand.get("inventory_verified_qty"),
            **landed,
        }

        if quantity < moq:
            shortfall = moq - quantity
            moq_msg = (
                f"[MOQ] {sku_name}: 需求 {quantity} < MOQ {moq}，缺口 {shortfall}，建议拼单"
            )
            if offer_appendix:
                moq_msg += f" {offer_appendix}"
            log.append(moq_msg)
            return {
                **result_base,
                "status": "bundling",
                "reason": f"数量不足 MOQ（{quantity}/{moq}），需拼单 {shortfall} 件",
                "moq": moq,
                "shortfall": shortfall,
            }

        missing_certs = [c for c in certs_req if c not in cand_certs]
        if missing_certs:
            cert_msg = f"[CERT] {sku_name}: 缺少认证 {missing_certs}，推荐平替"
            if offer_appendix:
                cert_msg += f" {offer_appendix}"
            log.append(cert_msg)
            return {
                **result_base,
                "status": "alternative",
                "reason": f"缺少认证: {', '.join(missing_certs)}",
                "missing_certs": missing_certs,
            }

        if budget_usd > 0 and landed["landed_usd"] > budget_usd:
            over_pct = round((landed["landed_usd"] - budget_usd) / budget_usd * 100, 1)
            bud_msg = (
                f"[BUDGET] {sku_name}: 落地价 ${landed['landed_usd']} 超预算 ${budget_usd} "
                f"({over_pct}%)，推荐替代"
            )
            if offer_appendix:
                bud_msg += f" {offer_appendix}"
            log.append(bud_msg)
            return {
                **result_base,
                "status": "alternative",
                "reason": f"超预算 {over_pct}%（落地价 ${landed['landed_usd']} vs 预算 ${budget_usd}）",
                "over_budget_pct": over_pct,
            }

        ok_msg = (
            f"[OK] {sku_name} @ {supplier_name}: 落地价 ${landed['landed_usd']} "
            f"{shipping_term} — 通过"
        )
        if offer_appendix:
            ok_msg += f" 报价须含: {offer_appendix}"
        log.append(ok_msg)
        return {
            **result_base,
            "status": "approved",
            "reason": "全部条件满足",
        }

    @staticmethod
    def _select_shipping_term(destination: str) -> str:
        """基于目的地启发式选择贸易术语"""
        from modules.supply_chain.fx_service import _REGION_MAP
        region = _REGION_MAP.get(destination, "")
        if region in ("Africa", "South America", "Middle East"):
            return "CIF"
        return "FOB"
