"""
agents.procurement_graph — Buy-side 采购蜂群子图 (背靠背套利引擎)
═══════════════════════════════════════════════════════════════════
对标彭博级交易系统的 Buy-side 采购对冲 (Bloomberg 强一致性版本):

流程::

    ScoutNode → BiddingNode (async) → ArbitrageEvaluator → [spread > 5%?]
                                                              ├─ Yes → LockOrder + Persist (strict)
                                                              └─ No  → HedgeFailed 熔断

强一致性约束:
  - 全局统一 Event Loop，无 asyncio.new_event_loop() 反模式
  - BiddingNode 使用原生 async + asyncio.gather
  - DB 写入失败 → DatabaseOperationalError → 级联熔断
  - matched_trade_id 必须从 Sell-side 注入，否则 TransactionContextMissing
  - 所有 PO 生成 SHA-256 防篡改哈希
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from core.logger import get_logger
from core.ticker_plant import EventType, MarketEvent, get_market_bus

logger = get_logger(__name__)

# 最低套利率阈值
MIN_ARBITRAGE_PCT = 5.0


# ═══════════════════════════════════════════════════════════════
#  Exceptions
# ═══════════════════════════════════════════════════════════════

class HedgeFailed(Exception):
    """套利失败异常 — 上游成本过高，无法锁定利润"""

    def __init__(self, reason: str, spread_pct: float = 0.0) -> None:
        self.reason = reason
        self.spread_pct = spread_pct
        super().__init__(reason)


class DatabaseOperationalError(Exception):
    """数据库操作失败 — 强一致性要求下不允许静默吞错"""

    def __init__(self, operation: str, cause: Exception) -> None:
        self.operation = operation
        self.cause = cause
        super().__init__(f"[FATAL] {operation}: {cause}")


class TransactionContextMissing(Exception):
    """Sell-side 交易 ID 缺失 — 无法建立背靠背对应关系"""

    def __init__(self, detail: str = "") -> None:
        super().__init__(
            f"TransactionContextMissing: Sell-side transaction_id 未注入 — {detail}"
        )


# ═══════════════════════════════════════════════════════════════
#  State
# ═══════════════════════════════════════════════════════════════

class ProcurementState(TypedDict, total=False):
    """采购蜂群子图状态"""
    target_sku: dict[str, Any]
    required_qty: int
    max_cost_allowed: float
    sell_price_usd: float
    shipping_estimate_usd: float
    matched_trade_id: str                    # Sell-side 交易 ID (强制注入)
    supplier_quotes: list[dict[str, Any]]
    best_quote: dict[str, Any]
    arbitrage_result: dict[str, Any]
    final_po: dict[str, Any]
    status: str
    error: str


# ═══════════════════════════════════════════════════════════════
#  Mock Upstream Suppliers
# ═══════════════════════════════════════════════════════════════

_MOCK_UPSTREAM_SUPPLIERS = [
    {
        "supplier_id": "upstream-shenzhen-001",
        "supplier_name": "深圳华强北元器件总汇",
        "region": "Shenzhen",
        "credibility_score": 92.0,
        "specialties": ["capacitor", "resistor", "ic", "led"],
        "base_markup": 0.0,
    },
    {
        "supplier_id": "upstream-dongguan-002",
        "supplier_name": "东莞电子元件批发中心",
        "region": "Dongguan",
        "credibility_score": 85.0,
        "specialties": ["capacitor", "resistor", "connector"],
        "base_markup": -0.05,
    },
    {
        "supplier_id": "upstream-guangzhou-003",
        "supplier_name": "广州立创供应链",
        "region": "Guangzhou",
        "credibility_score": 88.0,
        "specialties": ["capacitor", "ic", "pcb", "led"],
        "base_markup": 0.03,
    },
]


async def _mock_supplier_quote(
    supplier: dict[str, Any],
    ticker_id: str,
    quantity: int,
    base_cost_hint: float,
) -> dict[str, Any]:
    """模拟上游供应商报价 (含随机延迟模拟网络)"""
    await asyncio.sleep(random.uniform(0.05, 0.2))

    markup = supplier.get("base_markup", 0.0)
    jitter = random.uniform(-0.08, 0.08)
    cost_per_unit = round(base_cost_hint * (1 + markup + jitter), 4)
    total_cost = round(cost_per_unit * quantity, 2)

    return {
        "supplier_id": supplier["supplier_id"],
        "supplier_name": supplier["supplier_name"],
        "region": supplier["region"],
        "credibility_score": supplier["credibility_score"],
        "ticker_id": ticker_id,
        "unit_cost_usd": cost_per_unit,
        "total_cost_usd": total_cost,
        "quantity": quantity,
        "lead_days": random.randint(3, 14),
        "quoted_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
#  Nodes (全部原生 async — 无 new_event_loop 反模式)
# ═══════════════════════════════════════════════════════════════

def scout_node(state: ProcurementState) -> dict[str, Any]:
    """ScoutNode: 从本地检索 Top-3 匹配的上游供应商"""
    target = state.get("target_sku", {})
    category = target.get("category", "")

    matched = []
    for s in _MOCK_UPSTREAM_SUPPLIERS:
        specs = s.get("specialties", [])
        if category in specs or not category:
            matched.append(s)

    matched.sort(key=lambda x: x["credibility_score"], reverse=True)
    top3 = matched[:3]

    if not top3:
        return {
            "supplier_quotes": [],
            "status": "no_upstream_suppliers",
            "error": "未找到匹配的上游供应商",
        }

    logger.info("ScoutNode: 找到 %d 个上游供应商 (category=%s)", len(top3), category)
    return {
        "supplier_quotes": [{"supplier": s, "quote": None} for s in top3],
        "status": "suppliers_found",
    }


async def bidding_node(state: ProcurementState) -> dict[str, Any]:
    """BiddingNode: 原生 async — 并发向 Top-3 供应商发起询价 (asyncio.gather)

    不再创建新的 event loop，直接在当前 ainvoke 上下文中 await。
    """
    suppliers_data = state.get("supplier_quotes", [])
    target = state.get("target_sku", {})
    qty = state.get("required_qty", 0)
    sell_price = state.get("sell_price_usd", 0)

    if not suppliers_data:
        return {"status": "no_quotes", "error": "无供应商可询价"}

    base_cost_hint = sell_price * 0.7 / max(qty, 1) if sell_price > 0 else 0.3
    ticker_id = target.get("ticker_id", "")

    # 原生 asyncio.gather — 在同一个 event loop 中并发
    tasks = [
        _mock_supplier_quote(sd["supplier"], ticker_id, qty, base_cost_hint)
        for sd in suppliers_data
    ]
    quotes = await asyncio.gather(*tasks)

    quotes_list = list(quotes)
    quotes_list.sort(key=lambda q: q["total_cost_usd"])

    logger.info(
        "BiddingNode: 收到 %d 个报价, 最低=$%.2f 最高=$%.2f",
        len(quotes_list),
        quotes_list[0]["total_cost_usd"] if quotes_list else 0,
        quotes_list[-1]["total_cost_usd"] if quotes_list else 0,
    )

    return {
        "supplier_quotes": quotes_list,
        "best_quote": quotes_list[0] if quotes_list else {},
        "status": "quotes_received",
    }


async def arbitrage_evaluator(state: ProcurementState) -> dict[str, Any]:
    """ArbitrageEvaluator: 金融级核算节点 (原生 async)

    计算: Arbitrage_Spread = Sell_Price - Buy_Price - Estimated_Shipping
    利润率 > 5% → 锁单 + 严格持久化 (失败则 DatabaseOperationalError)
    利润率 ≤ 5% → hedge_failed
    """
    best = state.get("best_quote", {})
    sell_price = state.get("sell_price_usd", 0)
    shipping = state.get("shipping_estimate_usd", 0)
    matched_trade_id = state.get("matched_trade_id", "")

    if not best:
        return {
            "arbitrage_result": {"passed": False, "reason": "无有效报价"},
            "status": "hedge_failed",
            "error": "无有效上游报价",
        }

    buy_price = best.get("total_cost_usd", 0)
    spread = round(sell_price - buy_price - shipping, 2)
    spread_pct = round((spread / sell_price * 100) if sell_price > 0 else 0, 2)

    result = {
        "sell_price_usd": sell_price,
        "buy_price_usd": buy_price,
        "shipping_usd": shipping,
        "spread_usd": spread,
        "spread_pct": spread_pct,
        "min_required_pct": MIN_ARBITRAGE_PCT,
        "supplier_id": best.get("supplier_id", ""),
        "supplier_name": best.get("supplier_name", ""),
        "matched_trade_id": matched_trade_id,
    }

    if spread_pct >= MIN_ARBITRAGE_PCT:
        if not str(matched_trade_id or "").strip():
            raise TransactionContextMissing(
                "HEDGE_LOCKED 必须绑定 Sell-side matched_trade_id，禁止空 ID 锁单",
            )

        result["passed"] = True
        result["decision"] = "HEDGE_LOCKED"
        tid_short = matched_trade_id[:12] if len(matched_trade_id) >= 12 else matched_trade_id
        logger.info(
            "ArbitrageEvaluator: HEDGE_LOCKED spread=$%.2f (%.1f%%) supplier=%s trade=%s",
            spread, spread_pct, best.get("supplier_name", "?"), tid_short,
        )

        # 生成采购单
        po_id = str(uuid.uuid4())
        po_number = f"PO-BUY-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{po_id[:6].upper()}"
        po_data = {
            "po_id": po_id,
            "po_number": po_number,
            "matched_trade_id": matched_trade_id,
            "supplier_id": best.get("supplier_id", ""),
            "supplier_name": best.get("supplier_name", ""),
            "ticker_id": best.get("ticker_id", ""),
            "quantity": best.get("quantity", 0),
            "unit_cost_usd": best.get("unit_cost_usd", 0),
            "total_cost_usd": buy_price,
            "sell_price_usd": sell_price,
            "shipping_usd": shipping,
            "spread_usd": spread,
            "spread_pct": spread_pct,
            "lock_status": "locked",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # SHA-256 防篡改
        po_json = json.dumps(po_data, sort_keys=True, separators=(",", ":"), default=str)
        po_hash = hashlib.sha256(po_json.encode("utf-8")).hexdigest()
        po_data["po_hash"] = po_hash

        # 严格持久化 — 失败则抛出 DatabaseOperationalError
        await _persist_procurement_order_strict(po_data)

        # 广播 HEDGE_LOCKED 事件
        bus = get_market_bus()
        event = MarketEvent(
            event_type=EventType.HEDGE_LOCKED,
            ticker_id=po_data.get("ticker_id") or "SYSTEM",
            data={
                "action": "HEDGE_LOCKED",
                "spread_usd": spread,
                "spread_pct": spread_pct,
                "supplier": po_data.get("supplier_name", ""),
                "po_number": po_data.get("po_number", ""),
                "matched_trade_id": matched_trade_id,
            },
        )
        try:
            await bus.publish(event)
        except Exception as exc:
            logger.warning("HEDGE_LOCKED 广播失败: %s", exc)

        return {
            "arbitrage_result": result,
            "final_po": po_data,
            "status": "hedge_locked",
        }

    result["passed"] = False
    result["decision"] = "HEDGE_FAILED"
    logger.warning(
        "ArbitrageEvaluator: HEDGE_FAILED spread=$%.2f (%.1f%% < %.1f%%)",
        spread, spread_pct, MIN_ARBITRAGE_PCT,
    )

    return {
        "arbitrage_result": result,
        "status": "hedge_failed",
        "error": f"套利率不足: {spread_pct:.1f}% < {MIN_ARBITRAGE_PCT}%",
    }


async def _persist_procurement_order_strict(po_data: dict[str, Any]) -> None:
    """严格持久化采购锁单 — 失败则抛出 DatabaseOperationalError

    不允许 pass / warning 掩盖错误。DB 写入失败 = 级联熔断。
    """
    from sqlalchemy.exc import OperationalError

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            from database.models import AsyncSessionFactory
            from modules.supply_chain.models import ProcurementOrder

            async with AsyncSessionFactory() as session:
                entry = ProcurementOrder(
                    po_hash=po_data["po_hash"],
                    matched_trade_id=(po_data.get("matched_trade_id") or po_data.get("po_id", ""))[:36],
                    supplier_id=(po_data.get("supplier_id", "") or "unknown")[:36],
                    supplier_name=po_data.get("supplier_name", "")[:256],
                    ticker_id=(po_data.get("ticker_id", "") or "")[:64],
                    cost_price_usd=float(po_data.get("total_cost_usd", 0)),
                    sell_price_usd=float(po_data.get("sell_price_usd", 0)),
                    quantity=int(po_data.get("quantity", 0)),
                    shipping_estimate_usd=float(po_data.get("shipping_usd", 0)),
                    arbitrage_spread_usd=float(po_data.get("spread_usd", 0)),
                    arbitrage_pct=float(po_data.get("spread_pct", 0)),
                    lock_status="locked",
                    document_hash="",
                )
                session.add(entry)
                await session.commit()
            return  # 成功
        except OperationalError as exc:
            logger.error(
                "ProcurementOrder 持久化 OperationalError (attempt %d/%d): %s",
                attempt, max_retries, exc,
            )
            if attempt >= max_retries:
                raise DatabaseOperationalError("ProcurementOrder 持久化", exc) from exc
            await asyncio.sleep(0.05 * attempt)
        except DatabaseOperationalError:
            raise  # 直接向上传播
        except Exception as exc:
            # 非 OperationalError 的其他 DB 异常也必须严格上报
            raise DatabaseOperationalError("ProcurementOrder 持久化", exc) from exc


# ═══════════════════════════════════════════════════════════════
#  Sub-Graph Builder
# ═══════════════════════════════════════════════════════════════

def build_procurement_graph():
    """构建采购蜂群子图 (全原生 async)

    流程: scout → bidding (async) → arbitrage_evaluator (async) → END
    """
    from database.pg_checkpointer import get_pg_checkpointer_sync

    graph = StateGraph(ProcurementState)

    graph.add_node("scout_node", scout_node)
    graph.add_node("bidding_node", bidding_node)
    graph.add_node("arbitrage_evaluator", arbitrage_evaluator)

    graph.set_entry_point("scout_node")

    graph.add_conditional_edges(
        "scout_node",
        lambda s: "bid" if s.get("status") == "suppliers_found" else "finish",
        {"bid": "bidding_node", "finish": END},
    )

    graph.add_conditional_edges(
        "bidding_node",
        lambda s: "evaluate" if s.get("status") == "quotes_received" else "finish",
        {"evaluate": "arbitrage_evaluator", "finish": END},
    )

    graph.add_edge("arbitrage_evaluator", END)

    checkpointer = get_pg_checkpointer_sync()
    return graph.compile(checkpointer=checkpointer)


async def run_procurement_async(
    target_sku: dict[str, Any],
    required_qty: int,
    sell_price_usd: float,
    shipping_estimate_usd: float = 0.0,
    matched_trade_id: str = "",
) -> dict[str, Any]:
    """原生 async 执行采购蜂群子图

    Parameters
    ----------
    target_sku : dict
        {ticker_id, sku_name, category}
    required_qty : int
        需求数量
    sell_price_usd : float
        对外售价 (USD)
    shipping_estimate_usd : float
        预估运费 (USD)
    matched_trade_id : str
        Sell-side 交易 ID (强制注入)

    Returns
    -------
    dict
        子图最终状态

    Raises
    ------
    TransactionContextMissing
        matched_trade_id 为空
    DatabaseOperationalError
        DB 写入失败
    """
    if not matched_trade_id:
        raise TransactionContextMissing("run_procurement_async 调用时未提供 matched_trade_id")

    graph = build_procurement_graph()
    initial_state: ProcurementState = {
        "target_sku": target_sku,
        "required_qty": required_qty,
        "sell_price_usd": sell_price_usd,
        "max_cost_allowed": sell_price_usd * 0.95,
        "shipping_estimate_usd": shipping_estimate_usd,
        "matched_trade_id": matched_trade_id,
    }

    config = {"configurable": {"thread_id": f"procurement-{uuid.uuid4().hex[:8]}"}}
    result = await graph.ainvoke(initial_state, config=config)
    return dict(result)


def run_procurement_sync(
    target_sku: dict[str, Any],
    required_qty: int,
    sell_price_usd: float,
    shipping_estimate_usd: float = 0.0,
    matched_trade_id: str = "",
) -> dict[str, Any]:
    """同步桥接 — 已在运行中的 loop 时于独立线程内 asyncio.run，避免 loop 割裂。

    注意：协程必须在执行 asyncio.run 的同一线程创建，禁止把主线程 coroutine 交给工作线程。
    """
    import asyncio
    import concurrent.futures

    async def _run() -> dict[str, Any]:
        return await run_procurement_async(
            target_sku=target_sku,
            required_qty=required_qty,
            sell_price_usd=sell_price_usd,
            shipping_estimate_usd=shipping_estimate_usd,
            matched_trade_id=matched_trade_id,
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run())

    def _worker() -> dict[str, Any]:
        return asyncio.run(_run())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_worker).result(timeout=120)
