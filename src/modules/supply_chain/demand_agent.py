"""
modules.supply_chain.demand_agent — C端需求解析智能体
──────────────────────────────────────────────────────
职责：
  1. 接收海外买家的原始询盘文本（可能含拼写错误、非结构化描述）
  2. 调用 Ollama 提取结构化需求：产品类型/规格/数量/预算/目的地/认证要求
  3. 校验并持久化为 DemandOrder 记录
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from core.logger import get_logger

logger = get_logger(__name__)

_DEMAND_EXTRACT_PROMPT: str = """\
You are an electronic components procurement analyst.
Parse the buyer's message and extract a structured demand. Output ONLY valid JSON:

{
  "product_keywords": "short product description",
  "category": "capacitor|resistor|ic|led|connector|pcb|other",
  "specs": {"voltage": "...", "capacitance": "...", "package": "...", ...},
  "quantity": integer,
  "budget_usd": number or 0 if unknown,
  "destination": "country or city",
  "urgency": "low|medium|high",
  "certs_required": ["CE", "RoHS", ...],
  "buyer_name": "name if mentioned, else unknown",
  "buyer_country": "country if mentioned, else unknown"
}

Rules:
- Tolerate typos and messy English
- If budget is in another currency, convert roughly to USD
- If specs are vague, include what you can infer
- If quantity not specified, default to 0 (invalid)"""


class DemandAgent:
    """C端需求解析引擎"""

    async def execute(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        """解析买家需求并持久化

        Parameters
        ----------
        ctx : AgentContext
        params : dict
            - raw_input: str — 买家原始询盘文本
            OR structured fields directly

        Returns
        -------
        dict
            结构化需求 + demand_id + validation status
        """
        raw_input: str = params.get("raw_input", "")
        if not raw_input:
            return {"valid": False, "error": "缺少 raw_input 字段"}

        logger.info("需求解析启动: 输入长度=%d 字符", len(raw_input))

        demand = await self._extract_demand(raw_input)

        valid, error = self._validate(demand)
        if not valid:
            logger.warning("需求校验失败: %s", error)
            return {"valid": False, "error": error, "parsed": demand}

        demand_id = await self._persist(demand, raw_input)
        demand["demand_id"] = demand_id
        demand["valid"] = True

        logger.info(
            "需求解析完成: id=%s category=%s qty=%d budget=$%s",
            demand_id[:8], demand.get("category"), demand.get("quantity", 0),
            demand.get("budget_usd", 0),
        )
        return demand

    async def _extract_demand(self, raw_input: str) -> dict[str, Any]:
        llm = ChatOllama(model="qwen3:4b", temperature=0.1)
        response = llm.invoke([
            SystemMessage(content=_DEMAND_EXTRACT_PROMPT),
            HumanMessage(content=f"Buyer message:\n\n{raw_input}"),
        ])

        from agents.state import extract_json_from_llm
        try:
            return extract_json_from_llm(response.content)
        except ValueError:
            return {"product_keywords": raw_input[:100], "category": "other",
                    "specs": {}, "quantity": 0, "budget_usd": 0}

    @staticmethod
    def _validate(demand: dict) -> tuple[bool, str]:
        if not demand.get("product_keywords"):
            return False, "未能识别产品类型"
        qty = demand.get("quantity", 0)
        if not isinstance(qty, (int, float)) or qty <= 0:
            return False, f"数量无效: {qty}"
        return True, ""

    async def _persist(self, demand: dict, raw_input: str) -> str:
        demand_id = str(uuid.uuid4())
        try:
            from database.models import AsyncSessionFactory
            from modules.supply_chain.models import DemandOrder

            async with AsyncSessionFactory() as session:
                order = DemandOrder(
                    id=demand_id,
                    buyer_name=demand.get("buyer_name", "unknown"),
                    buyer_country=demand.get("buyer_country", "unknown"),
                    product_keywords=demand.get("product_keywords", ""),
                    specs_required=demand.get("specs"),
                    quantity=int(demand.get("quantity", 0)),
                    budget_usd=float(demand.get("budget_usd", 0)),
                    certs_required=demand.get("certs_required"),
                    destination=demand.get("destination", ""),
                    urgency=demand.get("urgency", "medium"),
                    raw_input=raw_input,
                    status="pending",
                )
                session.add(order)
                await session.commit()
        except Exception as exc:
            logger.error("需求入库失败: %s", exc)
        return demand_id
