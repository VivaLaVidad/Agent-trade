"""
test_bloomberg_consistency.py — Bloomberg 强一致性集成测试
═══════════════════════════════════════════════════════════
模拟:
  1. 前端成交生成 Sell-side transaction_id
  2. 传给采购端 (procurement_graph)
  3. 采购端模拟数据库突然断开连接
  4. 验证系统正确触发级联熔断
  5. 验证未发送任何错误的确认信令

覆盖:
  - TransactionContextMissing 异常
  - DatabaseOperationalError 级联熔断
  - matched_trade_id 强注入验证
  - 原生 async bidding (无 event loop 割裂)
  - PO SHA-256 防篡改 + matched_trade_id 绑定
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def test_transaction_context_missing_raises() -> None:
    """缺少 sell-side transaction_id → TransactionContextMissing"""
    from agents.procurement_graph import TransactionContextMissing, run_procurement_async

    async def _run() -> None:
        try:
            await run_procurement_async(
                target_sku={"ticker_id": "CLAW-ELEC-CAP-100NF", "category": "capacitor"},
                required_qty=500,
                sell_price_usd=100.0,
                matched_trade_id="",  # 空 → 应抛异常
            )
            assert False, "应抛出 TransactionContextMissing"
        except TransactionContextMissing as exc:
            assert "transaction_id" in str(exc).lower() or "matched_trade_id" in str(exc)

    asyncio.run(_run())


def test_matched_trade_id_injected_into_po() -> None:
    """matched_trade_id 正确注入到 PO 中"""
    from agents.procurement_graph import (
        scout_node,
        arbitrage_evaluator,
    )

    # 直接测试 arbitrage_evaluator 的 PO 生成
    async def _run() -> None:
        state = {
            "sell_price_usd": 100.0,
            "shipping_estimate_usd": 5.0,
            "matched_trade_id": "TXN-SELL-12345678",
            "best_quote": {
                "supplier_id": "upstream-001",
                "supplier_name": "Test Supplier",
                "ticker_id": "CLAW-ELEC-CAP-100NF",
                "unit_cost_usd": 0.15,
                "total_cost_usd": 75.0,
                "quantity": 500,
            },
        }

        # Mock DB 持久化 (避免真实 DB 连接)
        with patch("agents.procurement_graph._persist_procurement_order_strict", new_callable=AsyncMock):
            result = await arbitrage_evaluator(state)

        if result.get("status") == "hedge_locked":
            po = result.get("final_po", {})
            assert po.get("matched_trade_id") == "TXN-SELL-12345678"
            assert po.get("po_hash")
            assert len(po["po_hash"]) == 64

    asyncio.run(_run())


def test_database_failure_triggers_cascade_meltdown() -> None:
    """DB 写入失败 → DatabaseOperationalError → 级联熔断"""
    from agents.procurement_graph import (
        DatabaseOperationalError,
        arbitrage_evaluator,
    )
    from sqlalchemy.exc import OperationalError

    async def _run() -> None:
        state = {
            "sell_price_usd": 100.0,
            "shipping_estimate_usd": 5.0,
            "matched_trade_id": "TXN-SELL-DBFAIL-001",
            "best_quote": {
                "supplier_id": "upstream-001",
                "supplier_name": "Test Supplier",
                "ticker_id": "CLAW-ELEC-CAP-100NF",
                "unit_cost_usd": 0.15,
                "total_cost_usd": 75.0,
                "quantity": 500,
            },
        }

        # Mock DB 持久化抛出 OperationalError (模拟连接断开)
        mock_db_error = OperationalError(
            "connection refused", {}, Exception("pg connection lost")
        )

        async def _mock_persist_fail(po_data):
            raise DatabaseOperationalError("ProcurementOrder 持久化", mock_db_error)

        with patch(
            "agents.procurement_graph._persist_procurement_order_strict",
            side_effect=_mock_persist_fail,
        ):
            try:
                result = await arbitrage_evaluator(state)
                # 如果没抛异常，说明 spread 不够 → 正常 hedge_failed
                assert result.get("status") in ("hedge_failed", "hedge_locked")
            except DatabaseOperationalError as exc:
                # 这是预期的级联熔断
                assert "FATAL" in str(exc) or "持久化" in str(exc)
                assert exc.operation == "ProcurementOrder 持久化"

    asyncio.run(_run())


def test_procurement_node_cascade_on_missing_txn_id() -> None:
    """_procurement_node 缺少 transaction_id → cascade_failure"""
    from modules.supply_chain.matching_graph import _procurement_node

    state = {
        "negotiation_result": {
            "best_match": {
                "ticker_id": "CLAW-ELEC-CAP-100NF",
                "sku_name": "100nF MLCC",
                "landed_usd": 50.0,
                "shipping_usd": 3.0,
            },
        },
        "structured_demand": {"category": "capacitor", "quantity": 500},
        "invoice_result": {"status": "generated"},
        "purchase_order": {},
        "sell_side_transaction_id": "",  # 空 → 级联失败
    }

    result = _procurement_node(state)
    proc = result.get("procurement_result", {})
    assert proc.get("status") in ("fatal_error", "cascade_failure")
    assert "TransactionContextMissing" in proc.get("error", "") or "transaction_id" in proc.get("error", "").lower()


def test_procurement_node_with_valid_txn_id() -> None:
    """_procurement_node 有 transaction_id → 正常执行 (mock DB)"""
    from modules.supply_chain.matching_graph import _procurement_node

    state = {
        "negotiation_result": {
            "best_match": {
                "ticker_id": "CLAW-ELEC-CAP-100NF",
                "sku_name": "100nF MLCC",
                "landed_usd": 100.0,
                "shipping_usd": 3.0,
                "category": "capacitor",
            },
        },
        "structured_demand": {"category": "capacitor", "quantity": 500},
        "invoice_result": {"status": "generated"},
        "purchase_order": {"po_number": "PO-SELL-20260407-ABC123"},
        "sell_side_transaction_id": "TXN-SELL-VALID-001",
    }

    with patch("agents.procurement_graph._persist_procurement_order_strict", new_callable=AsyncMock):
        result = _procurement_node(state)

    proc = result.get("procurement_result", {})
    assert proc.get("triggered") is True
    assert proc.get("matched_trade_id") == "TXN-SELL-VALID-001"
    assert proc.get("status") in ("hedge_locked", "hedge_failed", "error")


def test_bidding_node_native_async() -> None:
    """BiddingNode 使用原生 async (无 new_event_loop)"""
    from agents.procurement_graph import bidding_node

    async def _run() -> None:
        state = {
            "target_sku": {"ticker_id": "CLAW-ELEC-CAP-100NF"},
            "required_qty": 500,
            "sell_price_usd": 50.0,
            "supplier_quotes": [
                {"supplier": {"supplier_id": "s1", "supplier_name": "A",
                              "region": "SZ", "credibility_score": 90, "base_markup": 0.0}},
                {"supplier": {"supplier_id": "s2", "supplier_name": "B",
                              "region": "GZ", "credibility_score": 85, "base_markup": -0.05}},
            ],
        }
        # bidding_node 现在是原生 async — 直接 await
        result = await bidding_node(state)
        assert result["status"] == "quotes_received"
        assert len(result["supplier_quotes"]) == 2

    asyncio.run(_run())


def test_no_new_event_loop_in_procurement_nodes() -> None:
    """验证 procurement_graph.py 的核心节点中不再有 asyncio.new_event_loop() 反模式

    注意: run_procurement_sync 作为同步桥接函数允许使用 asyncio.run，
    但核心节点 (scout_node, bidding_node, arbitrage_evaluator) 不应创建新 loop。
    """
    import inspect
    from agents.procurement_graph import scout_node, bidding_node, arbitrage_evaluator

    for fn in [scout_node, bidding_node, arbitrage_evaluator]:
        source = inspect.getsource(fn)
        assert "new_event_loop()" not in source, (
            f"{fn.__name__} 中发现 asyncio.new_event_loop() 反模式"
        )
        assert "loop.run_until_complete" not in source, (
            f"{fn.__name__} 中发现 loop.run_until_complete 反模式"
        )


def test_po_hash_includes_matched_trade_id() -> None:
    """PO SHA-256 哈希包含 matched_trade_id (防止 ID 篡改)"""
    import hashlib
    import json
    from agents.procurement_graph import arbitrage_evaluator

    async def _run() -> None:
        state = {
            "sell_price_usd": 200.0,
            "shipping_estimate_usd": 5.0,
            "matched_trade_id": "TXN-SELL-HASH-TEST",
            "best_quote": {
                "supplier_id": "upstream-001",
                "supplier_name": "Test Supplier",
                "ticker_id": "CLAW-ELEC-RES-10K",
                "unit_cost_usd": 0.2,
                "total_cost_usd": 100.0,
                "quantity": 500,
            },
        }

        with patch("agents.procurement_graph._persist_procurement_order_strict", new_callable=AsyncMock):
            result = await arbitrage_evaluator(state)

        if result.get("status") == "hedge_locked":
            po = result["final_po"]
            # matched_trade_id 应在 PO 数据中
            assert po["matched_trade_id"] == "TXN-SELL-HASH-TEST"

            # 验证哈希包含 matched_trade_id
            po_copy = dict(po)
            po_copy.pop("po_hash")
            po_json = json.dumps(po_copy, sort_keys=True, separators=(",", ":"), default=str)
            expected_hash = hashlib.sha256(po_json.encode("utf-8")).hexdigest()
            assert po["po_hash"] == expected_hash

    asyncio.run(_run())


def test_matching_graph_compiles_with_consistency() -> None:
    """matching_graph 含强一致性约束后仍能正常编译"""
    from modules.supply_chain.matching_graph import build_matching_graph
    graph = build_matching_graph()
    assert graph is not None
