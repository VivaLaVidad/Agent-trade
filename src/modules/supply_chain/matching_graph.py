"""
modules.supply_chain.matching_graph — LangGraph 全球工业品撮合工作流
──────────────────────────────────────────────────────────────────
流程：
  START → demand_node（C端需求解析）
        → supply_node（B端供应链检索 / RAG）
        → risk_defense_node（价格波动熔断 + 库存 Agent，见 agents.agent_workflow）
        → negotiate_node（贸易谈判决策树 + 阶梯报价）
        → tiered_quote_node（阶梯报价看板生成 + 谈判状态初始化）
        → [has_tiered_quotes?]
            ├─ Yes → [buyer_accepts?]
            │          ├─ Yes → po_gen_node（生成采购订单）→ END
            │          └─ No  → END（返回阶梯报价看板，等待买家选择）
            └─ No  → END（返回替代/拼单方案）
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from core.logger import get_logger

logger = get_logger(__name__)


class MatchState(TypedDict, total=False):
    """撮合工作流全局状态"""
    raw_input: str
    structured_demand: dict[str, Any]
    candidates: list[dict[str, Any]]
    risk_alerts: list[str]
    negotiation_result: dict[str, Any]
    tiered_quotes: list[dict[str, Any]]
    negotiation_status: str          # pending / counter_offer / accepted / rejected
    negotiation_round: int           # 当前谈判轮次
    buyer_selection: dict[str, Any]  # 买家选择的报价方案
    purchase_order: dict[str, Any]
    status: str
    error: str


def demand_node(state: MatchState) -> dict[str, Any]:
    """C端需求解析节点（同步包装异步 DemandAgent）"""
    import asyncio
    from modules.supply_chain.demand_agent import DemandAgent

    agent = DemandAgent()
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            agent.execute(ctx=None, params={"raw_input": state["raw_input"]}),
        )
    finally:
        loop.close()

    if not result.get("valid", False):
        return {"structured_demand": result, "status": "demand_invalid",
                "error": result.get("error", "需求解析失败")}

    return {"structured_demand": result, "status": "demand_parsed"}


def supply_node(state: MatchState) -> dict[str, Any]:
    """B端供应链检索节点"""
    import asyncio
    from modules.supply_chain.supply_agent import SupplyAgent

    demand = state.get("structured_demand", {})
    agent = SupplyAgent()
    loop = asyncio.new_event_loop()
    try:
        candidates = loop.run_until_complete(
            agent.execute(ctx=None, params={
                "category": demand.get("category", ""),
                "specs": demand.get("specs", {}),
                "certs_required": demand.get("certs_required", []),
                "budget_usd": demand.get("budget_usd", 0),
                "quantity": demand.get("quantity", 0),
                "top_n": 5,
            }),
        )
    finally:
        loop.close()

    if not candidates:
        return {"candidates": [], "status": "no_candidates", "error": "未找到匹配供应商"}

    return {"candidates": candidates, "status": "candidates_found"}


def risk_defense_node(state: MatchState) -> dict[str, Any]:
    """RAG/检索之后：价格波动熔断 + 库存 Agent 校验，写回 candidates 与 risk_alerts"""
    from agents.agent_workflow import apply_risk_defense_to_candidates

    cands = state.get("candidates") or []
    if not cands:
        return {"risk_alerts": []}

    apply_risk_defense_to_candidates(cands)
    alerts: list[str] = []
    for c in cands:
        if c.get("abnormal_quote_risk"):
            nm = (c.get("sku_name") or "?")[:48]
            alerts.append(
                f"异常报价风险: {nm} (标价偏离历史均价 {c.get('price_deviation_vs_hist_pct')}%)",
            )
        if c.get("inventory_low_stock"):
            nm = (c.get("sku_name") or "?")[:48]
            alerts.append(
                f"库存紧缺声明: {nm} 核实 {c.get('inventory_verified_qty', 0)}pcs",
            )
    return {"candidates": cands, "risk_alerts": alerts, "status": "candidates_found"}


def negotiate_node(state: MatchState) -> dict[str, Any]:
    """贸易谈判决策节点（含阶梯报价生成）"""
    import asyncio
    from modules.supply_chain.negotiator import NegotiatorAgent

    demand = state.get("structured_demand", {})
    candidates = state.get("candidates", [])

    agent = NegotiatorAgent()
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            agent.execute(ctx=None, demand=demand, candidates=candidates),
        )
    finally:
        loop.close()

    has_approved = result.get("best_match") is not None and \
                   result["best_match"].get("status") == "approved"

    return {
        "negotiation_result": result,
        "tiered_quotes": result.get("tiered_quotes", []),
        "status": "approved" if has_approved else "no_approval",
    }


def tiered_quote_node(state: MatchState) -> dict[str, Any]:
    """阶梯报价看板节点 + 谈判状态初始化

    从 negotiation_result 中提取阶梯报价，初始化谈判状态机。
    在自动模式下（无 buyer_selection），自动选择 Option A 继续。
    在交互模式下，返回阶梯报价看板等待买家选择。
    """
    from modules.supply_chain.negotiation_state import NegotiationStateMachine

    neg_result = state.get("negotiation_result", {})
    tiered = state.get("tiered_quotes", [])
    demand = state.get("structured_demand", {})
    buyer_selection = state.get("buyer_selection")

    if not tiered:
        logger.info("无阶梯报价，跳过报价看板节点")
        return {"negotiation_status": "no_quotes"}

    # 初始化谈判状态机
    best = neg_result.get("best_match", {})
    nsm = NegotiationStateMachine(
        match_id=best.get("sku_id", ""),
        demand_id=demand.get("demand_id", ""),
        merchant_id=demand.get("merchant_id", ""),
        client_id=demand.get("client_id", ""),
    )

    # 卖家提交初始报价（Option A 的价格）
    first_tier = tiered[0].get("tiers", [{}])[0] if tiered else {}
    seller_offer = {
        "unit_price_usd": first_tier.get("unit_price_usd", 0),
        "quantity": first_tier.get("quantity", 0),
        "shipping_term": first_tier.get("shipping_term", "FOB"),
        "landed_usd": first_tier.get("landed_usd", 0),
    }
    nsm.submit_seller_offer(seller_offer)

    # 自动模式：如果有 buyer_selection，处理买家选择
    if buyer_selection:
        selected_option = buyer_selection.get("option", "A")
        action = buyer_selection.get("action", "accept")

        if action == "accept":
            nsm.submit_buyer_response("accept")
            logger.info("买家接受报价: option=%s", selected_option)
            return {
                "negotiation_status": "accepted",
                "negotiation_round": nsm.current_round,
                "status": "approved",
            }
        elif action == "counter":
            nsm.submit_buyer_response("counter", buyer_selection.get("counter_offer"))
            return {
                "negotiation_status": "counter_offer",
                "negotiation_round": nsm.current_round,
            }
        else:
            nsm.submit_buyer_response("reject")
            return {
                "negotiation_status": "rejected",
                "negotiation_round": nsm.current_round,
                "status": "no_approval",
            }

    # 无 buyer_selection → 自动接受 Option A（演示/自动化模式）
    nsm.submit_buyer_response("accept")
    logger.info("自动模式: 默认接受 Option A 报价")
    return {
        "negotiation_status": "accepted",
        "negotiation_round": nsm.current_round,
        "status": "approved",
    }


_PO_PROMPT: str = """\
You are a procurement document specialist. Generate a professional Purchase Order \
based on the match data below. Include: PO number, date, buyer info, supplier info, \
item details (SKU, qty, unit price, total), shipping term, payment term, delivery date.

Output the PO as structured plain text. Do NOT output JSON."""


def po_gen_node(state: MatchState) -> dict[str, Any]:
    """采购订单生成节点"""
    neg = state.get("negotiation_result", {})
    demand = state.get("structured_demand", {})
    best = neg.get("best_match", {})

    if not best:
        return {"purchase_order": {}, "status": "no_po"}

    po_number = f"PO-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    offer_note = (best.get("offer_disclaimer") or "").strip()
    po_data = {
        "po_number": po_number,
        "sku_name": best.get("sku_name", ""),
        "supplier_name": best.get("supplier_name", ""),
        "quantity": demand.get("quantity", 0),
        "unit_price_rmb": best.get("unit_price_rmb", 0),
        "total_usd": best.get("landed_usd", 0),
        "fx_rate": best.get("fx_rate", 7.25),
        "shipping_term": best.get("shipping_term", "FOB"),
        "payment_term": "T/T 30% deposit, 70% before shipment",
        "buyer_name": demand.get("buyer_name", ""),
        "destination": demand.get("destination", ""),
        "offer_disclaimer": offer_note,
        "abnormal_quote_risk": best.get("abnormal_quote_risk", False),
    }

    try:
        llm = ChatOllama(model="qwen3:4b", temperature=0.3)
        resp = llm.invoke([
            SystemMessage(content=_PO_PROMPT),
            HumanMessage(
                content=(
                    f"Match data:\n{json.dumps(po_data, ensure_ascii=False, indent=2)}\n\n"
                    "If offer_disclaimer is non-empty, you MUST include it verbatim in the PO terms."
                ),
            ),
        ])
        content = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
        po_data["content"] = content
    except Exception as exc:
        logger.error("PO 文本生成失败: %s", exc)
        po_data["content"] = f"[Auto-generated PO]\n{json.dumps(po_data, indent=2)}"

    _persist_po(po_data, best)

    return {"purchase_order": po_data, "status": "po_generated"}


def _persist_po(po_data: dict, match_data: dict) -> None:
    """持久化采购订单（同步写入）"""
    try:
        import asyncio
        from database.models import AsyncSessionFactory
        from modules.supply_chain.models import PurchaseOrder, MatchResult

        async def _save():
            async with AsyncSessionFactory() as session:
                match_record = MatchResult(
                    demand_id=match_data.get("demand_id", str(uuid.uuid4())),
                    supplier_id=match_data.get("supplier_id", str(uuid.uuid4())),
                    sku_id=match_data.get("sku_id", ""),
                    match_score=match_data.get("match_score", 0),
                    quoted_price_usd=po_data.get("total_usd", 0),
                    shipping_term=po_data.get("shipping_term", "FOB"),
                    status="approved",
                )
                session.add(match_record)
                await session.flush()

                po = PurchaseOrder(
                    match_id=match_record.id,
                    po_number=po_data["po_number"],
                    items_json={"sku": po_data.get("sku_name"), "qty": po_data.get("quantity")},
                    total_rmb=po_data.get("unit_price_rmb", 0) * po_data.get("quantity", 0),
                    total_usd=po_data.get("total_usd", 0),
                    fx_rate=po_data.get("fx_rate", 7.25),
                    shipping_term=po_data.get("shipping_term", "FOB"),
                    payment_term=po_data.get("payment_term", ""),
                    content=po_data.get("content", ""),
                )
                session.add(po)
                await session.commit()

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_save())
        finally:
            loop.close()
    except Exception as exc:
        logger.error("PO 入库失败: %s", exc)


def _route_after_negotiation(state: MatchState) -> str:
    """谈判后路由：有阶梯报价 → 报价看板；否则直接结束"""
    tiered = state.get("tiered_quotes", [])
    if tiered:
        return "tiered_quotes"
    if state.get("status") == "approved":
        return "generate_po"
    return "finish"


def _route_after_tiered(state: MatchState) -> str:
    """阶梯报价后路由：accepted → 生成PO；其他 → 结束（等待买家选择）"""
    if state.get("status") == "approved":
        return "generate_po"
    return "finish"


def build_matching_graph():
    """构建撮合工作流 StateGraph

    流程：
      demand → supply → risk_defense → negotiate → tiered_quote → po_gen
    """
    graph = StateGraph(MatchState)

    graph.add_node("demand_node", demand_node)
    graph.add_node("supply_node", supply_node)
    graph.add_node("risk_defense_node", risk_defense_node)
    graph.add_node("negotiate_node", negotiate_node)
    graph.add_node("tiered_quote_node", tiered_quote_node)
    graph.add_node("po_gen_node", po_gen_node)

    graph.set_entry_point("demand_node")

    graph.add_conditional_edges(
        "demand_node",
        lambda s: "search" if s.get("status") == "demand_parsed" else "finish",
        {"search": "supply_node", "finish": END},
    )

    graph.add_conditional_edges(
        "supply_node",
        lambda s: "defend" if s.get("status") == "candidates_found" else "finish",
        {"defend": "risk_defense_node", "finish": END},
    )

    graph.add_edge("risk_defense_node", "negotiate_node")

    graph.add_conditional_edges(
        "negotiate_node",
        _route_after_negotiation,
        {"tiered_quotes": "tiered_quote_node", "generate_po": "po_gen_node", "finish": END},
    )

    graph.add_conditional_edges(
        "tiered_quote_node",
        _route_after_tiered,
        {"generate_po": "po_gen_node", "finish": END},
    )

    graph.add_edge("po_gen_node", END)

    memory = MemorySaver()
    return graph.compile(checkpointer=memory)


class MatchingOrchestrator:
    """撮合引擎编排器 —— 对外统一调用入口"""

    def __init__(self) -> None:
        self._graph = build_matching_graph()

    async def run(self, buyer_input: str, thread_id: str | None = None) -> dict[str, Any]:
        tid = thread_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": tid}}
        result = await self._graph.ainvoke({"raw_input": buyer_input}, config=config)
        return dict(result)
