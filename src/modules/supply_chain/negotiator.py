"""
modules.supply_chain.negotiator — 贸易谈判决策智能体（事件驱动 + Ticker 绑定）
═══════════════════════════════════════════════════════════════════════════════
职责：
  1. MOQ 校验：数量不足 → 拼单建议 (SuggestBundling)
  2. 认证校验：资质不符 → 推荐平替 (RecommendAlternative)
  3. 预算校验：超预算 → 替代方案 / 部分履约
  4. 贸易术语选择：基于目的地自动推荐 FOB / CIF
  5. 阶梯报价生成：为 approved 候选生成 Option A/B/C 多档报价
  6. **Ticker 绑定**: 所有候选 SKU 自动解析为标准化 Ticker ID
  7. **事件驱动**: 谈判期间异步订阅 MarketDataBus，波动率突变触发硬中断
  8. **幂等防护**: 波动率突变时调用 IdempotencyGuard 注销旧 Offer，重新生成
"""

from __future__ import annotations

import asyncio
from typing import Any

from modules.supply_chain.fx_service import FxRateService
from modules.supply_chain.tiered_quote import TieredQuoteEngine
from core.logger import get_logger
from core.ticker_plant import (
    EventType,
    MarketEvent,
    get_market_bus,
    get_ticker_registry,
)

logger = get_logger(__name__)


class NegotiatorAgent:
    """贸易条款谈判决策引擎（事件驱动 + Ticker 绑定）

    实现完整的决策树：MOQ → 认证 → 预算 → 贸易术语 → 阶梯报价
    每个失败分支都有回退策略（拼单/平替/部分履约）。
    通过的候选自动生成 3 档阶梯报价看板。

    事件驱动增强:
      - 谈判期间订阅相关 Ticker 的 VOLATILITY_SPIKE 事件
      - 波动率突变时硬中断当前谈判，注销旧 Offer，重新定价
      - 所有候选 SKU 自动绑定标准化 Ticker ID
    """

    def __init__(self) -> None:
        self._fx = FxRateService()
        self._tiered = TieredQuoteEngine()
        self._volatility_interrupted = False
        self._spike_event: MarketEvent | None = None

    async def execute(
        self,
        ctx: Any,
        demand: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """对每个候选 SKU 执行谈判决策树 + 阶梯报价 + 事件监听

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
              "volatility_interrupted": bool,
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

        # ── 1. 为所有候选 SKU 解析 Ticker ID ──
        registry = get_ticker_registry()
        ticker_ids: set[str] = set()
        for cand in candidates:
            ticker = registry.resolve(
                category=cand.get("category", cand.get("sku_name", "unknown")),
                name=cand.get("sku_name", ""),
                specs=cand.get("specs"),
            )
            cand["ticker_id"] = ticker.ticker_id
            ticker_ids.add(ticker.ticker_id)

        # ── 2. 订阅 MarketDataBus 监听波动率突变 ──
        bus = get_market_bus()
        self._volatility_interrupted = False
        self._spike_event = None

        async def _on_volatility_spike(event: MarketEvent) -> None:
            if event.event_type == EventType.VOLATILITY_SPIKE:
                if event.ticker_id in ticker_ids or event.ticker_id == "*":
                    self._volatility_interrupted = True
                    self._spike_event = event
                    logger.warning(
                        "谈判硬中断: 波动率突变 ticker=%s vol=%.4f",
                        event.ticker_id,
                        event.data.get("volatility_7d", 0),
                    )

        # 订阅所有相关 Ticker 的事件
        for tid in ticker_ids:
            bus.subscribe(tid, _on_volatility_spike)

        try:
            # ── 3. 执行谈判决策树 ──
            for cand in candidates:
                # 每轮开始让出事件循环，使 MarketDataBus 上已排队的 publish / 回调得以执行
                await asyncio.sleep(0)
                # 检查是否被波动率突变中断
                if self._volatility_interrupted:
                    log.append(
                        f"[INTERRUPT] 波动率突变硬中断 — "
                        f"ticker={self._spike_event.ticker_id if self._spike_event else '?'} "
                        f"vol={self._spike_event.data.get('volatility_7d', 0) if self._spike_event else 0:.4f}"
                    )
                    break

                result = self._evaluate_candidate(
                    cand, quantity, budget_usd, certs_req, destination, log,
                )

                if result["status"] == "approved":
                    approved.append(result)
                elif result["status"] == "alternative":
                    alternatives.append(result)
                elif result["status"] == "bundling":
                    bundling.append(result)

                # 下一候选评估前再次让出，避免与「仅轮首 sleep(0)」产生调度竞态
                await asyncio.sleep(0)

            # ── 4. 波动率中断处理: 注销旧 Offer + 重新定价 ──
            if self._volatility_interrupted and approved:
                log.append("[INTERRUPT] 注销所有已批准 Offer，基于新 Tick 数据重新定价...")
                await self._handle_volatility_interrupt(approved, demand, log)

            best = approved[0] if approved else (alternatives[0] if alternatives else None)

            # ── 5. 仅为正式 approved 候选生成阶梯报价 ──
            tiered_quotes: list[dict[str, Any]] = []
            if approved:
                approved_ids = {r.get("sku_id") for r in approved}
                full_candidates = [c for c in candidates if c.get("sku_id") in approved_ids]
                tiered_quotes = self._tiered.generate_multi_candidate_tiers(
                    full_candidates, demand, top_n=3,
                )
                for tq in tiered_quotes:
                    for tier in tq.get("tiers", []):
                        log.append(
                            f"[TIER] {TieredQuoteEngine.format_tier_display(tier)}"
                        )
            elif alternatives:
                log.append(
                    "[TIER] 无 approved 候选，跳过阶梯报价看板；"
                    "平替/超预算方案仅供人工或下轮谈判处理",
                )

            logger.info(
                "谈判完成: approved=%d alternatives=%d bundling=%d tiered=%d interrupted=%s",
                len(approved), len(alternatives), len(bundling),
                len(tiered_quotes), self._volatility_interrupted,
            )
            return {
                "best_match": best,
                "all_approved": approved,
                "alternatives": alternatives,
                "bundling_suggestions": bundling,
                "tiered_quotes": tiered_quotes,
                "negotiation_log": log,
                "volatility_interrupted": self._volatility_interrupted,
            }
        finally:
            # ── 清理订阅 ──
            for tid in ticker_ids:
                bus.unsubscribe(tid, _on_volatility_spike)

    async def _handle_volatility_interrupt(
        self,
        approved: list[dict],
        demand: dict[str, Any],
        log: list[str],
    ) -> None:
        """波动率突变中断处理: 注销旧 Offer + 重新定价

        调用 IdempotencyGuard 注销旧的 trade_id，
        然后基于新的 Tick 数据重新计算落地成本。
        """
        from core.security import get_idempotency_guard

        guard = get_idempotency_guard()
        quantity = int(demand.get("quantity", 0))
        destination = demand.get("destination", "")

        for offer in approved:
            # 注销旧 Offer 的幂等键
            old_trade_id = offer.get("_trade_id", "")
            if old_trade_id:
                await guard.release(old_trade_id)
                log.append(f"[INTERRUPT] 已注销旧 Offer trade_id={old_trade_id[:16]}...")

            # 重新计算落地成本
            shipping_term = offer.get("shipping_term", "FOB")
            unit_price = offer.get("unit_price_rmb", 0)
            new_landed = self._fx.calculate_landed_cost(
                unit_price, quantity, destination, shipping_term,
            )
            old_landed = offer.get("landed_usd", 0)
            offer.update(new_landed)

            delta_pct = 0
            if old_landed > 0:
                delta_pct = round((new_landed["landed_usd"] - old_landed) / old_landed * 100, 2)

            log.append(
                f"[REPRICE] {offer.get('sku_name', '?')} (ticker={offer.get('ticker_id', '?')}): "
                f"${old_landed:.2f} → ${new_landed['landed_usd']:.2f} ({delta_pct:+.2f}%)"
            )

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
        ticker_id = cand.get("ticker_id", "")
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
                f"[VOLATILITY] {sku_name} (ticker={ticker_id}): 异常报价风险 — "
                f"已调用 PriceVolatilityMonitor 二次确认 ({vm.get('note', 'mock')})",
            )

        result_base = {
            "sku_id": sku_id,
            "ticker_id": ticker_id,
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
                f"[MOQ] {sku_name} (ticker={ticker_id}): "
                f"需求 {quantity} < MOQ {moq}，缺口 {shortfall}，建议拼单"
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
            cert_msg = (
                f"[CERT] {sku_name} (ticker={ticker_id}): "
                f"缺少认证 {missing_certs}，推荐平替"
            )
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
                f"[BUDGET] {sku_name} (ticker={ticker_id}): "
                f"落地价 ${landed['landed_usd']} 超预算 ${budget_usd} ({over_pct}%)，推荐替代"
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
            f"[OK] {sku_name} (ticker={ticker_id}) @ {supplier_name}: "
            f"落地价 ${landed['landed_usd']} {shipping_term} — 通过"
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
