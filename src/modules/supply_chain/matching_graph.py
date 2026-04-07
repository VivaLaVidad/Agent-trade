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
    negotiation_result: dict[str, Any]
    tiered_quotes: list[dict[str, Any]]
    negotiation_status: str          # pending / counter_offer / accepted / rejected
    negotiation_round: int           # 当前谈判轮次
    buyer_selection: dict[str, Any]  # 买家选择的报价方案
    purchase_order: dict[str, Any]
    invoice_result: dict[str, Any]   # DocuForge 文档生成结果
    sell_side_transaction_id: str     # Sell-side 交易 ID (用于背靠背对应)
    procurement_result: dict[str, Any]  # Buy-side 采购对冲结果
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
            "transaction_id": po.get("po_number", ""),
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


def _procurement_node(state: MatchState) -> dict[str, Any]:
    """Buy-side 采购对冲节点: PI 发出后静默启动上游锁单 (强一致性版本)

    在 docuforge_node 生成 PI 后自动触发。
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
    """构建撮合工作流 StateGraph

    流程：
      demand → reg_guard → supply → [scout?] → risk_defense → negotiate → tiered_quote → po_gen → docuforge → procurement
    """
    from modules.compliance.export_control import reg_guard_node

    graph = StateGraph(MatchState)

    graph.add_node("demand_node", demand_node)
    graph.add_node("reg_guard_node", reg_guard_node)
    graph.add_node("supply_node", supply_node)
    graph.add_node("supply_scout_node", supply_scout_node)
    graph.add_node("risk_defense_node", risk_defense_node)
    graph.add_node("negotiate_node", negotiate_node)
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
        {"search": "supply_node", "finish": END},
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

    # po_gen → docuforge (自动生成 PI PDF)
    graph.add_edge("po_gen_node", "docuforge_node")

    # docuforge → procurement (背靠背套利: 静默启动上游锁单)
    graph.add_edge("docuforge_node", "procurement_node")
    graph.add_edge("procurement_node", END)

    checkpointer = get_pg_checkpointer_sync()
    return graph.compile(checkpointer=checkpointer)


class MatchingOrchestrator:
    """撮合引擎编排器 —— 对外统一调用入口"""

    def __init__(self) -> None:
        self._graph = build_matching_graph()

    async def run(self, buyer_input: str, thread_id: str | None = None) -> dict[str, Any]:
        tid = thread_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": tid}}
        result = await self._graph.ainvoke({"raw_input": buyer_input}, config=config)
        return dict(result)
