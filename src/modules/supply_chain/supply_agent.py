"""
modules.supply_chain.supply_agent — B端供应链智能体
───────────────────────────────────────────────────
职责：
  1. 接收结构化需求参数（品类/规格/认证/预算）
  2. 在 ProductSKU 表中进行参数化检索 + AI 模糊评分
  3. 返回 Top-N 候选 SKU（含供应商信息、价格、库存、认证匹配度）
  4. 提供 PriceMonitor 7 日均价趋势作为谈判参考
"""

from __future__ import annotations

import random
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from core.logger import get_logger

logger = get_logger(__name__)

_RANK_PROMPT: str = """\
You are an electronic components procurement specialist.
Given a buyer's requirement and a list of candidate SKUs, score each candidate 0-100.

Scoring criteria:
- Spec match (voltage, package, capacitance etc.): 40 points
- Certification match: 20 points
- Price competitiveness: 20 points
- Stock availability: 10 points
- Supplier rating: 10 points

Output ONLY a JSON array of objects: [{"sku_id": "...", "score": 85, "reason": "..."}]
Do NOT output anything else."""


class PriceMonitor:
    """价格波动监控（当前为 mock，返回模拟 7 日均价）"""

    @staticmethod
    def get_trend(sku_id: str, current_price: float) -> dict[str, Any]:
        volatility = random.uniform(-0.15, 0.10)
        avg_7d = round(current_price * (1 + volatility), 4)
        direction = "up" if avg_7d > current_price else "down" if avg_7d < current_price else "stable"
        return {
            "sku_id": sku_id,
            "current_price_rmb": current_price,
            "avg_7d_rmb": avg_7d,
            "trend": direction,
            "volatility_pct": round(volatility * 100, 1),
        }


class SupplyAgent:
    """B端供应链检索智能体"""

    async def execute(self, ctx: Any, params: dict[str, Any]) -> list[dict[str, Any]]:
        """检索匹配的供应商 SKU

        Parameters
        ----------
        ctx : AgentContext
        params : dict
            - category: str
            - specs: dict (voltage, package, capacitance, etc.)
            - certs_required: list[str]
            - budget_usd: float
            - quantity: int
            - top_n: int (default 5)

        Returns
        -------
        list[dict]
            Top-N 候选 SKU 详情
        """
        category: str = params.get("category", "")
        specs: dict = params.get("specs", {})
        certs_req: list = params.get("certs_required", [])
        top_n: int = params.get("top_n", 5)
        merchant_id: str = params.get("merchant_id", "")

        logger.info("供应链检索: category=%s merchant=%s", category, merchant_id or "all")

        raw_candidates = await self._query_skus(category, specs, merchant_id=merchant_id, limit=20)
        if not raw_candidates:
            logger.info("未找到匹配 SKU")
            return []

        ranked = await self._ai_rank(params, raw_candidates)
        result = ranked[:top_n]

        for item in result:
            item["price_trend"] = PriceMonitor.get_trend(
                item["sku_id"], item["unit_price_rmb"],
            )

        logger.info("供应链检索完成: 候选 %d / 返回 %d", len(raw_candidates), len(result))
        return result

    async def _query_skus(
        self, category: str, specs: dict, merchant_id: str = "", limit: int = 20,
    ) -> list[dict[str, Any]]:
        try:
            from database.models import AsyncSessionFactory
            from modules.supply_chain.models import ProductSKU, Supplier
            from sqlalchemy import select
            from sqlalchemy.orm import joinedload

            async with AsyncSessionFactory() as session:
                stmt = (
                    select(ProductSKU)
                    .options(joinedload(ProductSKU.supplier))
                    .where(ProductSKU.is_active.is_(True))
                )
                if category:
                    stmt = stmt.where(ProductSKU.category == category)
                if merchant_id:
                    stmt = stmt.join(Supplier).where(Supplier.merchant_id == merchant_id)

                stmt = stmt.limit(limit)
                result = await session.execute(stmt)
                rows = result.unique().scalars().all()

                return [
                    {
                        "sku_id": r.id,
                        "sku_name": r.name,
                        "brand": r.brand,
                        "category": r.category,
                        "specs": r.specs or {},
                        "unit_price_rmb": r.unit_price_rmb,
                        "moq": r.moq,
                        "stock_qty": r.stock_qty,
                        "certifications": r.certifications or [],
                        "supplier_id": r.supplier_id,
                        "supplier_name": r.supplier.name if r.supplier else "",
                        "supplier_region": r.supplier.region if r.supplier else "",
                        "supplier_rating": r.supplier.rating if r.supplier else 0,
                        "supplier_certs": r.supplier.certifications if r.supplier else [],
                    }
                    for r in rows
                ]
        except Exception as exc:
            logger.error("SKU 查询失败: %s", exc)
            return []

    async def _ai_rank(
        self, demand: dict, candidates: list[dict],
    ) -> list[dict[str, Any]]:
        import json
        if not candidates:
            return []

        llm = ChatOllama(model="qwen3:4b", temperature=0.1, format="json")
        demand_summary = json.dumps({
            k: v for k, v in demand.items() if k in (
                "category", "specs", "certs_required", "quantity", "budget_usd",
            )
        }, ensure_ascii=False)

        candidates_summary = json.dumps([
            {"sku_id": c["sku_id"], "name": c["sku_name"], "brand": c["brand"],
             "specs": c["specs"], "price_rmb": c["unit_price_rmb"],
             "moq": c["moq"], "stock": c["stock_qty"],
             "certs": c["certifications"], "supplier_rating": c["supplier_rating"]}
            for c in candidates
        ], ensure_ascii=False)

        try:
            response = llm.invoke([
                SystemMessage(content=_RANK_PROMPT),
                HumanMessage(content=f"Buyer requirement:\n{demand_summary}\n\nCandidates:\n{candidates_summary}"),
            ])
            from agents.state import extract_json_from_llm
            scores_raw = extract_json_from_llm(response.content)

            if isinstance(scores_raw, dict):
                scores_raw = scores_raw.get("results", scores_raw.get("candidates", []))

            score_map = {s["sku_id"]: s.get("score", 50) for s in scores_raw if "sku_id" in s}
        except Exception as exc:
            logger.warning("AI 排序失败，使用默认评分: %s", exc)
            score_map = {}

        for c in candidates:
            c["match_score"] = score_map.get(c["sku_id"], 50)

        candidates.sort(key=lambda x: x["match_score"], reverse=True)
        return candidates
