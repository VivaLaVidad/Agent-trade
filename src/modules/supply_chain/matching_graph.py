"""
modules.supply_chain.matching_graph — LangGraph 全球工业品撮合工作流
──────────────────────────────────────────────────────────────────
流程：
  START → demand_node（C端需求解析）
        → reg_guard_node（出口管制 / 制裁黑名单）
        → supply_node（B端供应链检索 / RAG）
        → [supply_scout_node?]（本地无候选时幽灵矿工动态寻价）
        → risk_defense_node（价格波动熔断 + 库存 Agent）
        → negotiate_node（谈判 + EpisodicMemory 画像 markup）
        → tiered_quote_node（阶梯报价看板 + 谈判状态）
        → [has_tiered_quotes?]
            ├─ Yes → [buyer_accepts?]
            │          ├─ Yes → po_gen_node → docuforge_node → procurement_node（Buy-side 锁单）→ END
            │          └─ No  → END
            └─ No  → END

  reg_guard 命中制裁 → END（REG_DENIED）
  scout 仅降级消息 → END
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from database.pg_checkpointer import get_pg_checkpointer_sync
from core.logger import get_logger

logger = get_logger(__name__)


class MatchState(TypedDict, total=False):
    """撮合工作流全局状态"""
    raw_input: str
    structured_demand: dict[str, Any]
    candidates: list[dict[str, Any]]
    risk_alerts: list[str]
    reg_guard_result: dict[str, Any]  # RegGuard 出口管制检查结果
    scout_result: dict[str, Any]     # SupplyChainScout 动态寻价结果
    source_type: str                 # LOCAL_INVENTORY | REMOTE_ARBITRAGE
    buyer_confirmation: dict[str, Any]  # C-side confirmation (selected quote)
    llm_sourcing_result: dict[str, Any]  # LLM sourcing structured output
    scatter_quotes: list[dict[str, Any]]  # A2A protocol quotes
    negotiation_result: dict[str, Any]
    tiered_quotes: list[dict[str, Any]]
    negotiation_status: str          # pending / counter_offer / accepted / rejected
    negotiation_round: int           # 当前谈判轮次
    buyer_selection: dict[str, Any]  # 买家选择的报价方案
    soft_lock_result: dict[str, Any]  # 上游软锁定结果 (Two-Phase Commit Phase 1)
    purchase_order: dict[str, Any]
    invoice_result: dict[str, Any]   # DocuForge 文档生成结果
    sell_side_transaction_id: str     # Sell-side 交易 ID (用于背靠背对应)
    procurement_result: dict[str, Any]  # Buy-side 采购对冲结果
    status: str
    error: str


_LOCAL_PROFIT_THRESHOLD = 5.0  # Local inventory min profit margin %


def local_inventory_node(state: MatchState) -> dict[str, Any]:
    """LocalInventoryNode: real DB query (ProductSKU) with MockInventory fallback"""
    import asyncio
    from core.demo_config import is_demo_mode

    demand = state.get("structured_demand", {})
    category = demand.get("category", "")
    product_kw = demand.get("product_keywords", demand.get("product", category))
    qty = demand.get("quantity", 1)

    hits = []

    # Try real PostgreSQL query first (non-DEMO mode)
    if not is_demo_mode():
        try:
            async def _db_query():
                from database.models import AsyncSessionFactory
                from modules.supply_chain.models import ProductSKU
                from sqlalchemy import select
                for attempt in range(3):
                    try:
                        async with AsyncSessionFactory() as session:
                            stmt = select(ProductSKU).where(
                                ProductSKU.name.ilike(f"%{product_kw}%")
                            ).limit(10)
                            rows = (await session.execute(stmt)).scalars().all()
                            return [
                                {
                                    "sku_id": r.sku_id,
                                    "sku_name": r.name,
                                    "category": r.category or category,
                                    "stock_qty": r.stock_qty or 0,
                                    "cost_price": float(r.unit_price_rmb or 0) / 7.2,
                                    "suggested_sell_price": float(r.unit_price_rmb or 0) / 7.2 * 1.15,
                                    "location": "DB",
                                    "specs": r.specs or {},
                                    "profit_margin_pct": 15.0,
                                    "is_un_certified": True,
                                    "is_rcep_eligible": True,
                                }
                                for r in rows if (r.stock_qty or 0) >= qty
                            ]
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(0.5 * (attempt + 1))
                return []

            loop = asyncio.new_event_loop()
            try:
                hits = loop.run_until_complete(_db_query())
            finally:
                loop.close()
            if hits:
                logger.info("[LocalInventory] DB query returned %d hits", len(hits))
        except Exception as exc:
            logger.warning("[LocalInventory] DB query failed, falling back to MockInventory: %s", exc)
            hits = []

    # Fallback to MockInventory (DEMO mode or DB failure)
    if not hits:
        from database.mock_inventory import get_mock_inventory
        inventory = get_mock_inventory()
        hits = inventory.query(sku_name=product_kw or category, qty=qty, category=category)

    if not hits:
        logger.info("[LocalInventory] No local match for %s -> scatter", product_kw)
        return {
            "source_type": "REMOTE_ARBITRAGE",
            "candidates": [],
            "status": "no_local_inventory",
        }

    profitable = [h for h in hits if h.get("profit_margin_pct", 0) > _LOCAL_PROFIT_THRESHOLD]
    if not profitable:
        logger.info("[LocalInventory] Local hits but margin < %.1f%% -> scatter", _LOCAL_PROFIT_THRESHOLD)
        return {
            "source_type": "REMOTE_ARBITRAGE",
            "candidates": [],
            "status": "no_local_inventory",
        }

    candidates = []
    for h in profitable[:5]:
        candidates.append({
            "sku_id": h["sku_id"],
            "sku_name": h["sku_name"],
            "category": h["category"],
            "supplier_name": f"Local-{h['location']}",
            "unit_price_rmb": round(h["cost_price"] * 7.2, 2),
            "stock_qty": h["stock_qty"],
            "certifications": [],
            "match_score": 95,
            "specs": h.get("specs", {}),
            "source": "local_inventory",
            "cost_price_usd": h["cost_price"],
            "suggested_sell_price_usd": h["suggested_sell_price"],
            "profit_margin_pct": h["profit_margin_pct"],
            "is_un_certified": h.get("is_un_certified", True),
            "is_rcep_eligible": h.get("is_rcep_eligible", True),
        })

    logger.info("[LocalInventory] Found %d profitable SKUs (source=LOCAL_INVENTORY)", len(candidates))
    return {
        "source_type": "LOCAL_INVENTORY",
        "candidates": candidates,
        "status": "candidates_found",
    }



def llm_sourcing_node(state: MatchState) -> dict[str, Any]:
    """LLM Dynamic Sourcing: uses LLM to search Pearl River Delta supplier network.

    In DEMO mode, delegates to scatter_node logic (A2A simulation).
    In production, calls LLM with search tool to find real suppliers.
    Returns A2APayload-structured results.
    """
    import asyncio
    from decimal import Decimal
    from core.demo_config import is_demo_mode
    from models.a2a_protocol import A2APayload, AgentCard, TurnStatus

    demand = state.get("structured_demand", {})
    category = demand.get("category", "")
    product_kw = demand.get("product_keywords", demand.get("product", category))
    qty = demand.get("quantity", 1)
    txn_id = state.get("sell_side_transaction_id", "")

    logger.info("[LLM-Sourcing] Query: %s qty=%d", product_kw, qty)

    if not is_demo_mode():
        # Production: call LLM with structured output
        try:
            async def _llm_source():
                from langchain_ollama import ChatOllama
                from langchain_core.messages import HumanMessage, SystemMessage
                import json as _json

                llm = ChatOllama(model="qwen3:8b", temperature=0.1)
                prompt = f"""You are a Pearl River Delta electronics sourcing agent.
Find 1-3 suppliers for: {product_kw} (category: {category}, qty: {qty}).
Return ONLY a JSON array of objects with fields:
  agent_id, sku_name, unit_price_usd (number), available_qty (int), profit_margin_pct (number), region (SZ/GZ/SH)
Example: [{{"agent_id":"supplier-sz-01","sku_name":"100nF MLCC","unit_price_usd":0.008,"available_qty":50000,"profit_margin_pct":12.5,"region":"SZ"}}]"""

                resp = await llm.ainvoke([
                    SystemMessage(content="You are a sourcing agent. Return only valid JSON."),
                    HumanMessage(content=prompt),
                ])
                text = resp.content if hasattr(resp, 'content') else str(resp)
                # Extract JSON array
                import re
                match = re.search(r'\[.*\]', text, re.DOTALL)
                if match:
                    return _json.loads(match.group())
                return []

            loop = asyncio.new_event_loop()
            try:
                raw = loop.run_until_complete(asyncio.wait_for(_llm_source(), timeout=30.0))
            finally:
                loop.close()

            if raw:
                payloads = []
                for item in raw[:3]:
                    payloads.append(A2APayload(
                        agent_card=AgentCard(
                            agent_id=item.get("agent_id", "llm-sourced"),
                            capabilities=["quote"],
                            endpoint="llm://sourcing",
                        ),
                        negotiation_round=0,
                        turn_status=TurnStatus.OFFER,
                        proposed_price=Decimal(str(round(float(item.get("unit_price_usd", 0.1)), 4))),
                        moq=max(qty, 100),
                        sell_side_transaction_id=txn_id or f"llm-auto-{id(item)}",
                        sku_name=item.get("sku_name", product_kw),
                        available_qty=int(item.get("available_qty", 10000)),
                        profit_margin_pct=float(item.get("profit_margin_pct", 8.0)),
                    ))

                candidates = []
                quotes_raw = []
                for p in payloads:
                    price_f = float(p.proposed_price)
                    quotes_raw.append({
                        "node_id": p.agent_card.agent_id,
                        "region": p.agent_card.agent_id.split("-")[1].upper() if "-" in p.agent_card.agent_id else "SZ",
                        "sku_name": p.sku_name,
                        "unit_price_usd": price_f,
                        "available_qty": p.available_qty,
                        "profit_margin_pct": p.profit_margin_pct,
                        "sell_side_transaction_id": p.sell_side_transaction_id,
                        "turn_status": p.turn_status.value,
                        "negotiation_round": p.negotiation_round,
                    })
                    candidates.append({
                        "sku_id": f"llm-{p.agent_card.agent_id}",
                        "sku_name": p.sku_name,
                        "category": category,
                        "supplier_name": f"LLM-{p.agent_card.agent_id}",
                        "unit_price_rmb": round(price_f * 7.2, 2),
                        "stock_qty": p.available_qty,
                        "certifications": [],
                        "match_score": 80,
                        "specs": {},
                        "source": "llm_sourcing",
                        "cost_price_usd": price_f,
                        "suggested_sell_price_usd": round(price_f * (1 + p.profit_margin_pct / 100), 4),
                        "profit_margin_pct": p.profit_margin_pct,
                        "is_un_certified": True,
                        "is_rcep_eligible": True,
                    })

                logger.info("[LLM-Sourcing] Found %d suppliers via LLM", len(candidates))
                return {
                    "source_type": "REMOTE_ARBITRAGE",
                    "llm_sourcing_result": {"source": "llm", "count": len(candidates)},
                    "scatter_quotes": quotes_raw,
                    "candidates": candidates,
                    "status": "candidates_found",
                }
        except Exception as exc:
            logger.warning("[LLM-Sourcing] LLM failed, falling back to A2A scatter: %s", exc)

    # Fallback: delegate to scatter_node (A2A simulation)
    return scatter_node(state)


def buyer_confirmation_node(state: MatchState) -> dict[str, Any]:
    """Buyer Confirmation Node: receives C-side selection after graph resume.

    This node runs after the graph is resumed via confirm-trade API.
    It reads buyer_confirmation from state and prepares for negotiation.
    """
    confirmation = state.get("buyer_confirmation", {})
    selected_id = confirmation.get("selected_quote_id", "")
    candidates = state.get("candidates", [])

    if not selected_id and candidates:
        # Auto-select best candidate if no explicit selection
        selected = candidates[0]
        logger.info("[BuyerConfirmation] Auto-selected best candidate: %s", selected.get("sku_id"))
    else:
        selected = next((c for c in candidates if c.get("sku_id") == selected_id), candidates[0] if candidates else {})
        logger.info("[BuyerConfirmation] Buyer selected: %s", selected_id)

    if not selected:
        return {"status": "no_selection", "error": "No candidate selected"}

    return {
        "candidates": [selected],
        "status": "buyer_confirmed",
    }


def scatter_node(state: MatchState) -> dict[str, Any]:
    """ScatterNode: A2A protocol broadcast to external nodes, collect quotes

    2026 A2A Standard: structured handshake with AgentCard + A2APayload validation.
    Missing sell_side_transaction_id triggers TransactionContextMissing.
    """
    import asyncio
    from decimal import Decimal

    from models.a2a_protocol import A2APayload, AgentCard, TurnStatus

    demand = state.get("structured_demand", {})
    category = demand.get("category", "")
    product_kw = demand.get("product_keywords", demand.get("product", category))
    qty = demand.get("quantity", 1)
    txn_id = state.get("sell_side_transaction_id", "")

    logger.info("[ScatterNode] A2A broadcast: %s qty=%d txn=%s", product_kw, qty, txn_id[:16] if txn_id else "N/A")

    async def _a2a_broadcast():
        import random as _rnd
        external_nodes = [
            AgentCard(agent_id="ext-shenzhen-01", capabilities=["quote", "negotiate"], endpoint="a2a://sz-01.claw.internal"),
            AgentCard(agent_id="ext-guangzhou-02", capabilities=["quote"], endpoint="a2a://gz-02.claw.internal"),
            AgentCard(agent_id="ext-shanghai-03", capabilities=["quote", "hedge"], endpoint="a2a://sh-03.claw.internal"),
        ]
        latencies = {"ext-shenzhen-01": 0.8, "ext-guangzhou-02": 1.2, "ext-shanghai-03": 1.5}

        async def _query_node(card: AgentCard) -> A2APayload:
            await asyncio.sleep(latencies.get(card.agent_id, 1.0))
            base_price = _rnd.uniform(0.05, 2.0)
            margin = _rnd.uniform(3.0, 15.0)
            return A2APayload(
                agent_card=card,
                negotiation_round=0,
                turn_status=TurnStatus.OFFER,
                proposed_price=Decimal(str(round(base_price, 4))),
                moq=max(qty, 100),
                sell_side_transaction_id=txn_id or f"scatter-auto-{_rnd.randint(10000, 99999)}",
                sku_name=product_kw or category,
                available_qty=_rnd.randint(1000, 50000),
                profit_margin_pct=round(margin, 1),
            )

        return list(await asyncio.gather(*[_query_node(c) for c in external_nodes]))

    loop = asyncio.new_event_loop()
    try:
        payloads: list[A2APayload] = loop.run_until_complete(
            asyncio.wait_for(_a2a_broadcast(), timeout=5.0)
        )
    except asyncio.TimeoutError:
        payloads = []
    finally:
        loop.close()

    if not payloads:
        logger.warning("[ScatterNode] No A2A responses received")
        return {
            "source_type": "REMOTE_ARBITRAGE",
            "scatter_quotes": [],
            "candidates": [],
            "status": "no_candidates",
            "error": "External agents did not respond",
        }

    # Validate A2A payloads — reject missing txn_id
    from agents.procurement_graph import TransactionContextMissing
    validated: list[A2APayload] = []
    for p in payloads:
        if not p.sell_side_transaction_id or not p.sell_side_transaction_id.strip():
            raise TransactionContextMissing(
                f"A2A payload from {p.agent_card.agent_id} missing sell_side_transaction_id"
            )
        validated.append(p)

    # Convert validated A2APayloads to candidate format
    quotes_raw = []
    candidates = []
    for p in sorted(validated, key=lambda x: float(x.proposed_price)):
        region = p.agent_card.agent_id.split("-")[1] if "-" in p.agent_card.agent_id else "XX"
        price_f = float(p.proposed_price)
        quotes_raw.append({
            "node_id": p.agent_card.agent_id,
            "region": region.upper(),
            "sku_name": p.sku_name,
            "unit_price_usd": price_f,
            "suggested_sell_usd": round(price_f * (1 + p.profit_margin_pct / 100), 4),
            "available_qty": p.available_qty,
            "profit_margin_pct": p.profit_margin_pct,
            "negotiation_round": p.negotiation_round,
            "turn_status": p.turn_status.value,
            "sell_side_transaction_id": p.sell_side_transaction_id,
        })
        candidates.append({
            "sku_id": f"ext-{p.agent_card.agent_id}-{p.sku_name[:12]}".replace(" ", "_"),
            "sku_name": p.sku_name,
            "category": category,
            "supplier_name": f"External-{region.upper()}",
            "unit_price_rmb": round(price_f * 7.2, 2),
            "stock_qty": p.available_qty,
            "certifications": [],
            "match_score": 75,
            "specs": {},
            "source": "a2a_external",
            "cost_price_usd": price_f,
            "suggested_sell_price_usd": round(price_f * (1 + p.profit_margin_pct / 100), 4),
            "profit_margin_pct": p.profit_margin_pct,
        })

    logger.info("[ScatterNode] A2A validated %d payloads (source=REMOTE_ARBITRAGE)", len(candidates))
    return {
        "source_type": "REMOTE_ARBITRAGE",
        "scatter_quotes": quotes_raw,
        "candidates": candidates,
        "status": "candidates_found",
    }


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
        return {"candidates": [], "status": "no_candidates_local", "error": "本地库未找到匹配供应商"}

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
    每轮卖家/买家动作结束后尝试写入 ``negotiation_rounds`` 表。
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
            _persist_negotiation_rounds(nsm)
            return {
                "negotiation_status": "accepted",
                "negotiation_round": nsm.current_round,
                "status": "approved",
            }
        elif action == "counter":
            nsm.submit_buyer_response("counter", buyer_selection.get("counter_offer"))
            _persist_negotiation_rounds(nsm)
            return {
                "negotiation_status": "counter_offer",
                "negotiation_round": nsm.current_round,
            }
        else:
            nsm.submit_buyer_response("reject")
            _persist_negotiation_rounds(nsm)
            return {
                "negotiation_status": "rejected",
                "negotiation_round": nsm.current_round,
                "status": "no_approval",
            }

    # 无 buyer_selection → 自动接受 Option A（演示/自动化模式）
    nsm.submit_buyer_response("accept")
    logger.info("自动模式: 默认接受 Option A 报价")
    _persist_negotiation_rounds(nsm)
    return {
        "negotiation_status": "accepted",
        "negotiation_round": nsm.current_round,
        "status": "approved",
    }


def _persist_negotiation_rounds(nsm: Any) -> None:
    """将谈判状态机中已产生的轮次写入 negotiation_rounds 表（同步封装 async）。"""
    import asyncio

    if not getattr(nsm, "rounds", None):
        return

    async def _flush() -> None:
        for rec in nsm.rounds:
            await nsm.persist_round(rec)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_flush())
    except Exception as exc:
        logger.warning("谈判轮次持久化跳过: %s", exc)
    finally:
        loop.close()


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

    sell_side_transaction_id = str(uuid.uuid4())
    po_number = f"PO-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    offer_note = (best.get("offer_disclaimer") or "").strip()
    po_data = {
        "po_number": po_number,
        "transaction_id": sell_side_transaction_id,
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

    _persist_po(po_data, best, demand)

    return {
        "purchase_order": po_data,
        "sell_side_transaction_id": sell_side_transaction_id,
        "status": "po_generated",
    }


def _persist_po(po_data: dict, match_data: dict, demand: dict[str, Any]) -> None:
    """持久化采购订单与 Sell-side 流水（同步写入）

    PO / MatchResult 与 TransactionLedger 使用同一 ``po_data["transaction_id"]``，
    供 DocuForge 与 procurement 背靠背强关联。
    """
    try:
        import asyncio
        from database.models import AsyncSessionFactory
        from modules.supply_chain.models import PurchaseOrder, MatchResult

        async def _save_and_ledger() -> None:
            match_id_str: str
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
                match_id_str = str(match_record.id)

            txn = (po_data.get("transaction_id") or "").strip()
            if not txn:
                return
            try:
                from modules.supply_chain.ledger import LedgerService

                merchant = (
                    demand.get("merchant_id")
                    or match_data.get("merchant_id")
                    or "default"
                )
                merchant_id = str(merchant)[:36]
                client_id = str(demand.get("client_id") or "")[:36]
                ledger = LedgerService()
                record = ledger.create_transaction(
                    merchant_id=merchant_id,
                    client_id=client_id,
                    amount_usd=float(po_data.get("total_usd") or 0),
                    match_id=match_id_str,
                    po_number=str(po_data.get("po_number") or ""),
                    ticker_id=str(match_data.get("ticker_id") or ""),
                    transaction_id=txn,
                )
                await ledger.persist(record)
            except Exception as lex:
                logger.error(
                    "Sell-side 交易流水入库失败（PO 已提交）txn=%s: %s",
                    txn[:8] if len(txn) >= 8 else txn,
                    lex,
                )

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_save_and_ledger())
        finally:
            loop.close()
    except Exception as exc:
        logger.error("PO 入库失败: %s", exc)


def _route_after_negotiation(state: MatchState) -> str:
    """谈判后路由：仅正式 approved 且存在阶梯报价时才进报价看板，避免平替自动成交。"""
    tiered = state.get("tiered_quotes", [])
    approved_flow = state.get("status") == "approved"
    if approved_flow and tiered:
        return "tiered_quotes"
    if approved_flow:
        return "generate_po"
    return "finish"


def _route_after_tiered(state: MatchState) -> str:
    """阶梯报价后路由：accepted → 生成PO；其他 → 结束（等待买家选择）"""
    if state.get("status") == "approved":
        return "generate_po"
    return "finish"


def _docuforge_node(state: MatchState) -> dict[str, Any]:
    """DocuForge 节点: 交易达成时自动生成 Proforma Invoice PDF + SHA-256 哈希"""
    import asyncio

    neg = state.get("negotiation_result", {})
    demand = state.get("structured_demand", {})
    best = neg.get("best_match", {})
    po = state.get("purchase_order", {})

    if not best or state.get("status") != "po_generated":
        return {"invoice_result": {"status": "skipped"}}

    try:
        from modules.documents.invoice_generator import get_invoice_generator

        generator = get_invoice_generator()
        txn_data = {
            "po_number": po.get("po_number", ""),
            "ticker_id": best.get("ticker_id", ""),
            "sku_name": best.get("sku_name", ""),
            "quantity": demand.get("quantity", 0),
            "unit_price_rmb": best.get("unit_price_rmb", 0),
            "unit_price_usd": best.get("landed_usd", 0) / max(demand.get("quantity", 1), 1),
            "total_usd": best.get("total_usd", best.get("landed_usd", 0)),
            "shipping_usd": best.get("shipping_usd", 0),
            "landed_usd": best.get("landed_usd", 0),
            "routing_fee_usd": round(best.get("landed_usd", 0) * 0.01, 2),
            "fee_rate": 0.01,
            "fx_rate": best.get("fx_rate", 7.25),
            "shipping_term": best.get("shipping_term", "FOB"),
            "payment_term": po.get("payment_term", "T/T 30% deposit"),
            "moq": best.get("moq", 100),
            "supplier_name": best.get("supplier_name", ""),
            "buyer_name": demand.get("buyer_name", ""),
            "destination": demand.get("destination", ""),
            "client_id": demand.get("client_id", ""),
            "transaction_id": po.get("transaction_id") or po.get("po_number", ""),
            "offer_disclaimer": best.get("offer_disclaimer", ""),
        }

        result = generator.generate_pi(txn_data)

        # 持久化哈希
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(generator.hash_and_persist(result))
        except Exception as exc:
            logger.warning("DocuForge 哈希持久化跳过: %s", exc)
        finally:
            loop.close()

        return {
            "invoice_result": {
                "status": "generated",
                "document_hash": result.get("document_hash", ""),
                "pdf_generated": result.get("pdf_bytes") is not None,
                "pdf_path": result.get("pdf_path", ""),
                "po_number": result.get("po_number", ""),
            },
        }
    except Exception as exc:
        logger.error("DocuForge 节点异常: %s", exc)
        return {"invoice_result": {"status": "error", "error": str(exc)}}


_STALE_DAYS = 7  # 底价数据过期天数
_SCOUT_DEGRADATION_MSG = (
    "该型号为特殊缺货件，我们的供应链专员正在全球询价，"
    "预计 2 小时内给您准确报价"
)


def supply_scout_node(state: MatchState) -> dict[str, Any]:
    """SupplyChainScout 节点: 本地库无结果或数据过期时，触发幽灵矿工动态寻价

    触发条件:
      - 本地 supply_node 返回 no_candidates_local
      - 或候选数据过期超过 7 天 (TODO: 检查 created_at)

    降级策略:
      - 抓取被反爬拦截或超时 → 返回优雅降级消息
      - 成功获取 → 转换为候选格式，合并到 candidates
    """
    import asyncio

    candidates = state.get("candidates", [])
    demand = state.get("structured_demand", {})
    status = state.get("status", "")

    # 仅在本地库无结果时触发
    if status != "no_candidates_local" and candidates:
        return {"scout_result": {"triggered": False, "reason": "local_data_sufficient"}}

    category = demand.get("category", "")
    product_kw = demand.get("product_keywords", demand.get("product", category))
    query = f"{product_kw} {category}".strip()

    if not query:
        return {
            "scout_result": {"triggered": True, "reason": "empty_query", "quotes": []},
            "status": "no_candidates",
            "error": "未找到匹配供应商（查询为空）",
        }

    logger.info("SupplyChainScout 触发: query=%s reason=%s", query[:40], status)

    try:
        from rpa_engine.supply_miner import get_supply_miner

        miner = get_supply_miner()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(miner.mine(query))
        finally:
            loop.close()

        if result.error or not result.quotes:
            # 抓取失败 → 优雅降级
            logger.warning("SupplyChainScout 降级: error=%s", result.error or "no_quotes")
            return {
                "scout_result": {
                    "triggered": True,
                    "source": result.source,
                    "error": result.error,
                    "degradation_msg": _SCOUT_DEGRADATION_MSG,
                },
                "status": "scout_degraded",
                "error": _SCOUT_DEGRADATION_MSG,
            }

        # 转换 SupplierQuote → 候选格式 (兼容 NegotiatorAgent)
        new_candidates = []
        for q in result.quotes:
            new_candidates.append({
                "sku_id": f"scout-{q.supplier_name[:8]}-{q.component_name[:12]}".replace(" ", "_"),
                "sku_name": q.component_name,
                "category": category,
                "supplier_name": q.supplier_name,
                "unit_price_rmb": q.unit_price_rmb,
                "moq": q.moq,
                "stock_qty": 10000 if q.stock_status == "in_stock" else 500 if q.stock_status == "low_stock" else 0,
                "certifications": [],
                "match_score": 70,
                "specs": {},
                "source": "ghost_miner",
                "price_tiers": q.price_tiers,
                "scraped_at": q.scraped_at,
            })

        logger.info(
            "SupplyChainScout 成功: query=%s quotes=%d source=%s elapsed=%.2fs",
            query[:40], len(new_candidates), result.source, result.elapsed_sec,
        )

        return {
            "candidates": new_candidates,
            "scout_result": {
                "triggered": True,
                "source": result.source,
                "quotes_count": len(new_candidates),
                "elapsed_sec": result.elapsed_sec,
            },
            "status": "candidates_found",
        }

    except Exception as exc:
        logger.error("SupplyChainScout 异常: %s", exc)
        return {
            "scout_result": {
                "triggered": True,
                "error": str(exc),
                "degradation_msg": _SCOUT_DEGRADATION_MSG,
            },
            "status": "scout_degraded",
            "error": _SCOUT_DEGRADATION_MSG,
        }


# ═══════════════════════════════════════════════════════════════
#  Margin Override Registry (Task 2 — Bloomberg OVRD command)
# ═══════════════════════════════════════════════════════════════

_margin_overrides: dict[str, float] = {}


def _upstream_soft_lock_node(state: MatchState) -> dict[str, Any]:
    """Two-Phase Commit Phase 1: 上游软锁定节点

    在 negotiate_node 之后、tiered_quote_node 之前执行。
    调用 procurement_graph.run_procurement_sync() 进行预询价，
    获取上游 24h 价格锁定 (soft lock)。

    - status=hedge_locked → 继续流转到 tiered_quote
    - 其他 → END with error "upstream_lock_failed"
    """
    from agents.procurement_graph import run_procurement_sync

    neg = state.get("negotiation_result", {})
    demand = state.get("structured_demand", {})
    best = neg.get("best_match", {})

    if not best:
        return {
            "soft_lock_result": {"status": "skipped", "reason": "no_best_match"},
            "status": "upstream_lock_failed",
            "error": "upstream_lock_failed",
        }

    target_sku = {
        "ticker_id": best.get("ticker_id", ""),
        "sku_name": best.get("sku_name", ""),
        "category": demand.get("category", ""),
    }
    sell_price = best.get("landed_usd", 0)
    qty = demand.get("quantity", 0)
    shipping = best.get("shipping_usd", 0)

    # Generate a pre-inquiry trade ID for the soft lock phase
    soft_lock_trade_id = f"SOFT-{uuid.uuid4().hex[:12].upper()}"

    try:
        result = run_procurement_sync(
            target_sku=target_sku,
            required_qty=qty,
            sell_price_usd=sell_price,
            shipping_estimate_usd=shipping,
            matched_trade_id=soft_lock_trade_id,
        )

        procurement_status = result.get("status", "unknown")
        arb = result.get("arbitrage_result", {})

        logger.info(
            "UpstreamSoftLock: status=%s spread=$%.2f (%.1f%%)",
            procurement_status,
            arb.get("spread_usd", 0),
            arb.get("spread_pct", 0),
        )

        if procurement_status == "hedge_locked":
            return {
                "soft_lock_result": {
                    "status": "hedge_locked",
                    "soft_lock_trade_id": soft_lock_trade_id,
                    "spread_usd": arb.get("spread_usd", 0),
                    "spread_pct": arb.get("spread_pct", 0),
                    "supplier_name": arb.get("supplier_name", ""),
                },
            }

        return {
            "soft_lock_result": {
                "status": procurement_status,
                "error": result.get("error", "upstream_lock_failed"),
            },
            "status": "upstream_lock_failed",
            "error": "upstream_lock_failed",
        }

    except Exception as exc:
        logger.error("UpstreamSoftLock 异常: %s", exc)
        return {
            "soft_lock_result": {"status": "error", "error": str(exc)},
            "status": "upstream_lock_failed",
            "error": "upstream_lock_failed",
        }


def _route_after_soft_lock(state: MatchState) -> str:
    """软锁定后路由: hedge_locked → 继续; 其他 → END"""
    sl = state.get("soft_lock_result", {})
    if sl.get("status") == "hedge_locked":
        return "continue"
    return "finish"


def _procurement_node(state: MatchState) -> dict[str, Any]:
    """Buy-side 采购对冲节点: Hard Commit — 确认已软锁定的上游订单

    Two-Phase Commit Phase 2: docuforge 生成 PI 后执行最终确认。
    强一致性约束:
      - 必须从 MatchState 提取 sell_side_transaction_id
      - 缺失则抛出 TransactionContextMissing
      - DB 写入失败 → DatabaseOperationalError → 级联熔断
      - 级联熔断时 PI 发送也会被标记为无效
    """
    from agents.procurement_graph import (
        DatabaseOperationalError,
        TransactionContextMissing,
        run_procurement_sync,
    )

    neg = state.get("negotiation_result", {})
    demand = state.get("structured_demand", {})
    best = neg.get("best_match", {})
    invoice = state.get("invoice_result", {})

    # 仅在 PI 已生成且有成交时触发
    if not best or invoice.get("status") != "generated":
        return {"procurement_result": {"triggered": False, "reason": "no_pi_generated"}}

    # 强制提取 Sell-side transaction_id
    sell_txn_id = state.get("sell_side_transaction_id", "")
    if not sell_txn_id:
        # 演示降级：无 transaction_id 时用 PO 号占位（生产应对接真实 ledger transaction_id）
        po = state.get("purchase_order", {})
        sell_txn_id = (po.get("transaction_id") or po.get("po_number") or "").strip()

    if not sell_txn_id:
        logger.error("[FATAL] TransactionContextMissing: 无法建立背靠背对应关系")
        return {
            "procurement_result": {
                "triggered": True,
                "status": "fatal_error",
                "error": "TransactionContextMissing: Sell-side transaction_id 未注入",
            },
            "status": "cascade_failure",
            "error": "TransactionContextMissing",
        }

    try:
        target_sku = {
            "ticker_id": best.get("ticker_id", ""),
            "sku_name": best.get("sku_name", ""),
            "category": demand.get("category", ""),
        }
        sell_price = best.get("landed_usd", 0)
        qty = demand.get("quantity", 0)
        shipping = best.get("shipping_usd", 0)

        result = run_procurement_sync(
            target_sku=target_sku,
            required_qty=qty,
            sell_price_usd=sell_price,
            shipping_estimate_usd=shipping,
            matched_trade_id=sell_txn_id,
        )

        procurement_status = result.get("status", "unknown")
        arb = result.get("arbitrage_result", {})
        final_po = result.get("final_po", {})

        logger.info(
            "采购对冲: status=%s spread=$%.2f (%.1f%%) trade=%s",
            procurement_status,
            arb.get("spread_usd", 0),
            arb.get("spread_pct", 0),
            sell_txn_id[:12],
        )

        return {
            "procurement_result": {
                "triggered": True,
                "status": procurement_status,
                "matched_trade_id": sell_txn_id,
                "arbitrage_spread_usd": arb.get("spread_usd", 0),
                "arbitrage_spread_pct": arb.get("spread_pct", 0),
                "supplier_name": arb.get("supplier_name", ""),
                "po_hash": final_po.get("po_hash", ""),
                "po_number": final_po.get("po_number", ""),
            },
        }

    except DatabaseOperationalError as exc:
        # DB 写入失败 → 级联熔断: PI 也标记为无效
        logger.error(
            "[FATAL] 账本写入失败，终止套利对冲: %s (trade=%s)",
            exc, sell_txn_id[:12],
        )
        return {
            "procurement_result": {
                "triggered": True,
                "status": "cascade_failure",
                "error": f"[FATAL] 账本写入失败: {exc}",
                "matched_trade_id": sell_txn_id,
            },
            "status": "cascade_failure",
            "error": f"[FATAL] DatabaseOperationalError: {exc}",
        }

    except TransactionContextMissing as exc:
        logger.error("[FATAL] %s", exc)
        return {
            "procurement_result": {
                "triggered": True,
                "status": "fatal_error",
                "error": str(exc),
            },
            "status": "cascade_failure",
            "error": str(exc),
        }

    except Exception as exc:
        logger.error("采购对冲节点异常: %s", exc)
        return {
            "procurement_result": {
                "triggered": True,
                "status": "error",
                "error": str(exc),
            },
        }


def build_matching_graph():
    """构建撮合工作流 StateGraph (Two-Phase Commit)

    流程：
      demand → reg_guard → supply → [scout?] → risk_defense
        → negotiate → soft_lock → tiered_quote → po_gen
        → docuforge → procurement(hard commit) → END
    """
    from modules.compliance.export_control import reg_guard_node

    graph = StateGraph(MatchState)

    graph.add_node("demand_node", demand_node)
    graph.add_node("reg_guard_node", reg_guard_node)
    graph.add_node("local_inventory_node", local_inventory_node)
    graph.add_node("scatter_node", scatter_node)
    graph.add_node("llm_sourcing_node", llm_sourcing_node)
    graph.add_node("buyer_confirmation_node", buyer_confirmation_node)
    graph.add_node("supply_node", supply_node)
    graph.add_node("supply_scout_node", supply_scout_node)
    graph.add_node("risk_defense_node", risk_defense_node)
    graph.add_node("negotiate_node", negotiate_node)
    graph.add_node("soft_lock_node", _upstream_soft_lock_node)
    graph.add_node("tiered_quote_node", tiered_quote_node)
    graph.add_node("po_gen_node", po_gen_node)
    graph.add_node("docuforge_node", _docuforge_node)
    graph.add_node("procurement_node", _procurement_node)

    graph.set_entry_point("demand_node")

    # demand → reg_guard (出口管制检查)
    graph.add_conditional_edges(
        "demand_node",
        lambda s: "reg_check" if s.get("status") == "demand_parsed" else "finish",
        {"reg_check": "reg_guard_node", "finish": END},
    )

    # reg_guard → supply (通过) | END (拦截)
    graph.add_conditional_edges(
        "reg_guard_node",
        lambda s: "search" if s.get("status") != "reg_denied" else "finish",
        {"search": "local_inventory_node", "finish": END},
    )


    # local_inventory -> risk_defense (LOCAL hit) | scatter (no local) | END
    def _route_after_local_inventory(s: MatchState) -> str:
        if s.get("status") == "candidates_found" and s.get("source_type") == "LOCAL_INVENTORY":
            return "defend"
        if s.get("status") == "no_local_inventory":
            return "llm_source"
        return "finish"

    graph.add_conditional_edges(
        "local_inventory_node",
        _route_after_local_inventory,
        {"defend": "risk_defense_node", "llm_source": "llm_sourcing_node", "finish": END},
    )


    # llm_sourcing -> risk_defense (got quotes) | scatter (LLM failed)
    def _route_after_llm_sourcing(s: MatchState) -> str:
        if s.get("status") == "candidates_found":
            return "defend"
        return "scatter_fallback"

    graph.add_conditional_edges(
        "llm_sourcing_node",
        _route_after_llm_sourcing,
        {"defend": "risk_defense_node", "scatter_fallback": "scatter_node"},
    )

    # scatter -> supply_node (fallback) | risk_defense (got quotes) | END
    def _route_after_scatter(s: MatchState) -> str:
        if s.get("status") == "candidates_found":
            return "defend"
        return "supply_fallback"

    graph.add_conditional_edges(
        "scatter_node",
        _route_after_scatter,
        {"defend": "risk_defense_node", "supply_fallback": "supply_node"},
    )

    # supply → risk_defense (有候选) | scout (无候选) | END (降级失败)
    def _route_after_supply(s: MatchState) -> str:
        if s.get("status") == "candidates_found":
            return "defend"
        if s.get("status") == "no_candidates_local":
            return "scout"
        return "finish"

    graph.add_conditional_edges(
        "supply_node",
        _route_after_supply,
        {"defend": "risk_defense_node", "scout": "supply_scout_node", "finish": END},
    )

    # scout → risk_defense (成功) | END (降级)
    graph.add_conditional_edges(
        "supply_scout_node",
        lambda s: "defend" if s.get("status") == "candidates_found" else "finish",
        {"defend": "risk_defense_node", "finish": END},
    )

    graph.add_edge("risk_defense_node", "negotiate_node")

    # negotiate → soft_lock (Two-Phase Commit Phase 1)
    graph.add_conditional_edges(
        "negotiate_node",
        _route_after_negotiation,
        {"tiered_quotes": "soft_lock_node", "generate_po": "soft_lock_node", "finish": END},
    )

    # soft_lock → tiered_quote (locked) | END (lock failed)
    graph.add_conditional_edges(
        "soft_lock_node",
        _route_after_soft_lock,
        {"continue": "tiered_quote_node", "finish": END},
    )

    graph.add_conditional_edges(
        "tiered_quote_node",
        _route_after_tiered,
        {"generate_po": "po_gen_node", "finish": END},
    )

    # po_gen → docuforge (自动生成 PI PDF)
    graph.add_edge("po_gen_node", "docuforge_node")

    # docuforge → procurement (Two-Phase Commit Phase 2: hard commit confirmation)
    graph.add_edge("docuforge_node", "procurement_node")
    graph.add_edge("procurement_node", END)

    checkpointer = get_pg_checkpointer_sync()
    return graph.compile(checkpointer=checkpointer, interrupt_before=["risk_defense_node"])


class MatchingOrchestrator:
    """撮合引擎编排器 —— 对外统一调用入口"""

    def __init__(self) -> None:
        self._graph = build_matching_graph()

    async def run(self, buyer_input: str, thread_id: str | None = None) -> dict[str, Any]:
        tid = thread_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": tid}}
        result = await self._graph.ainvoke({"raw_input": buyer_input}, config=config)
        return dict(result)
