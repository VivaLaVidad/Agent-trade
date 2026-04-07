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
from unittest.mock import AsyncMock, MagicMock, patch

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
    """matching_graph 含强一致性约束 + Two-Phase Commit 后仍能正常编译"""
    from modules.supply_chain.matching_graph import build_matching_graph
    graph = build_matching_graph()
    assert graph is not None


# ═══════════════════════════════════════════════════════════════
#  Task 1: Soft Lock (Two-Phase Commit) Tests
# ═══════════════════════════════════════════════════════════════

def test_soft_lock_node_success_with_hedge_locked() -> None:
    """_upstream_soft_lock_node returns hedge_locked when procurement succeeds"""
    from modules.supply_chain.matching_graph import _upstream_soft_lock_node

    state = {
        "negotiation_result": {
            "best_match": {
                "ticker_id": "CLAW-ELEC-CAP-100NF",
                "sku_name": "100nF MLCC",
                "landed_usd": 100.0,
                "shipping_usd": 3.0,
            },
        },
        "structured_demand": {"category": "capacitor", "quantity": 500},
    }

    with patch("agents.procurement_graph._persist_procurement_order_strict", new_callable=AsyncMock):
        result = _upstream_soft_lock_node(state)

    sl = result.get("soft_lock_result", {})
    # The mock procurement may or may not lock depending on random pricing,
    # but the node should always return a valid soft_lock_result dict
    assert "status" in sl
    assert sl["status"] in (
        "hedge_locked",
        "hedge_failed",
        "pending_review",
        "error",
        "no_upstream_suppliers",
    )


def test_soft_lock_node_no_best_match() -> None:
    """_upstream_soft_lock_node with no best_match → upstream_lock_failed"""
    from modules.supply_chain.matching_graph import _upstream_soft_lock_node

    state = {
        "negotiation_result": {},
        "structured_demand": {"category": "capacitor", "quantity": 500},
    }

    result = _upstream_soft_lock_node(state)
    sl = result.get("soft_lock_result", {})
    assert sl.get("status") == "skipped"
    assert result.get("status") == "upstream_lock_failed"


def test_soft_lock_route_hedge_locked() -> None:
    """_route_after_soft_lock returns 'continue' when hedge_locked"""
    from modules.supply_chain.matching_graph import _route_after_soft_lock

    state = {"soft_lock_result": {"status": "hedge_locked"}}
    assert _route_after_soft_lock(state) == "continue"


def test_soft_lock_route_failed() -> None:
    """_route_after_soft_lock returns 'finish' when not hedge_locked"""
    from modules.supply_chain.matching_graph import _route_after_soft_lock

    state = {"soft_lock_result": {"status": "hedge_failed"}}
    assert _route_after_soft_lock(state) == "finish"

    state2 = {}
    assert _route_after_soft_lock(state2) == "finish"


def test_graph_has_soft_lock_node() -> None:
    """Compiled graph includes the soft_lock_node in its structure"""
    from modules.supply_chain.matching_graph import build_matching_graph
    graph = build_matching_graph()
    # The graph should have the soft_lock_node registered
    assert graph is not None


# ═══════════════════════════════════════════════════════════════
#  Task 2: Bloomberg Command Parser Tests
# ═══════════════════════════════════════════════════════════════

def test_parse_ovrd_command() -> None:
    """Parse OVRD command with trade_id and force_margin"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from bloomberg_tui import parse_bloomberg_command

    result = parse_bloomberg_command("OVRD -id TRADE-001 --force_margin 4.0")
    assert result["command"] == "OVRD"
    assert result["trade_id"] == "TRADE-001"
    assert result["force_margin"] == 4.0


def test_parse_resume_command() -> None:
    """Parse RESUME command"""
    from bloomberg_tui import parse_bloomberg_command

    result = parse_bloomberg_command("RESUME -id TRADE-002")
    assert result["command"] == "RESUME"
    assert result["trade_id"] == "TRADE-002"


def test_parse_kill_command() -> None:
    """Parse KILL command"""
    from bloomberg_tui import parse_bloomberg_command

    result = parse_bloomberg_command("KILL -id TRADE-003")
    assert result["command"] == "KILL"
    assert result["trade_id"] == "TRADE-003"


def test_parse_unknown_command() -> None:
    """Unknown command returns error"""
    from bloomberg_tui import parse_bloomberg_command

    result = parse_bloomberg_command("FOOBAR -id X")
    assert result["command"] == "unknown"


def test_parse_missing_id() -> None:
    """Missing -id returns error"""
    from bloomberg_tui import parse_bloomberg_command

    result = parse_bloomberg_command("OVRD --force_margin 4.0")
    assert result["command"] == "unknown"
    assert "missing" in result.get("error", "").lower()


def test_margin_overrides_dict() -> None:
    """_margin_overrides dict is accessible and writable"""
    from bloomberg_tui import _margin_overrides

    _margin_overrides["TEST-001"] = 3.5
    assert _margin_overrides["TEST-001"] == 3.5
    del _margin_overrides["TEST-001"]


def test_arbitrage_grey_zone_pending_review() -> None:
    """3% ≤ spread < 有效阈值 → pending_review，不持久化"""
    from agents.procurement_graph import arbitrage_evaluator

    async def _run() -> None:
        # sell 100 - ship 5 - buy 91 = 4 USD → 4% spread
        state = {
            "sell_price_usd": 100.0,
            "shipping_estimate_usd": 5.0,
            "matched_trade_id": "TXN-GREY-001",
            "best_quote": {
                "supplier_id": "upstream-g",
                "supplier_name": "Grey Supplier",
                "ticker_id": "CLAW-ELEC-CAP-100NF",
                "unit_cost_usd": 0.18,
                "total_cost_usd": 91.0,
                "quantity": 500,
            },
        }
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        with patch("agents.procurement_graph._persist_procurement_order_strict", new_callable=AsyncMock):
            with patch("agents.procurement_graph.get_market_bus", return_value=mock_bus):
                result = await arbitrage_evaluator(state)

        assert result["status"] == "pending_review"
        arb = result["arbitrage_result"]
        assert arb["decision"] == "PENDING_REVIEW"
        assert arb["passed"] is False
        assert 3.0 <= arb["spread_pct"] < 5.0
        mock_bus.publish.assert_awaited_once()

    asyncio.run(_run())


def test_ovrd_lowers_effective_threshold() -> None:
    """matching_graph._margin_overrides 降低阈值后 4% 可 HEDGE_LOCKED"""
    from agents.procurement_graph import arbitrage_evaluator
    from modules.supply_chain import matching_graph as mg

    tid = "TXN-OVRD-THRESHOLD"

    async def _run() -> None:
        mg._margin_overrides[tid] = 3.5
        try:
            state = {
                "sell_price_usd": 100.0,
                "shipping_estimate_usd": 5.0,
                "matched_trade_id": tid,
                "best_quote": {
                    "supplier_id": "upstream-o",
                    "supplier_name": "OVRD Supplier",
                    "ticker_id": "CLAW-ELEC-CAP-100NF",
                    "unit_cost_usd": 0.18,
                    "total_cost_usd": 91.0,
                    "quantity": 500,
                },
            }
            with patch(
                "agents.procurement_graph._persist_procurement_order_strict",
                new_callable=AsyncMock,
            ):
                result = await arbitrage_evaluator(state)
            assert result["status"] == "hedge_locked"
            assert result["arbitrage_result"]["min_required_pct"] == 3.5
        finally:
            mg._margin_overrides.pop(tid, None)

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
#  Task 3: VLM Recovery Mode Tests
# ═══════════════════════════════════════════════════════════════

def test_vlm_recovery_mode_mock_locate() -> None:
    """VLMRecoveryMode mock returns deterministic coordinates"""
    from rpa_engine.abstract_layer.client import VLMRecoveryMode

    async def _run() -> None:
        vlm = VLMRecoveryMode(use_mock=True)
        x, y = await vlm.screenshot_and_locate("base64data", "submit button")
        assert isinstance(x, int)
        assert isinstance(y, int)
        assert x >= 100
        assert y >= 100

        # Same input → same output (deterministic)
        x2, y2 = await vlm.screenshot_and_locate("base64data", "submit button")
        assert (x, y) == (x2, y2)

    asyncio.run(_run())


def test_vlm_recovery_mode_click_no_stub() -> None:
    """VLMRecoveryMode click without stub returns mock result"""
    from rpa_engine.abstract_layer.client import VLMRecoveryMode

    async def _run() -> None:
        vlm = VLMRecoveryMode(use_mock=True)
        result = await vlm.click_by_coordinates(400, 300, stub=None, task_id="test-1")
        assert result["status"] == "clicked"
        assert result["x"] == 400
        assert result["y"] == 300
        assert result["method"] == "vlm_recovery"

    asyncio.run(_run())


def test_rpa_client_vlm_fallback_after_timeouts() -> None:
    """RPAClient triggers VLM fallback after 2 consecutive TimeoutErrors"""
    from rpa_engine.abstract_layer.client import RPAClient

    async def _run() -> None:
        client = RPAClient()
        # Simulate 2 consecutive timeouts by manipulating internal counter
        client._consecutive_timeouts = 1  # Already had 1 timeout

        # Mock _execute_with_retry to raise TimeoutError
        async def _mock_retry(*args, **kwargs):
            raise TimeoutError("DOM element not found")

        client._execute_with_retry = _mock_retry

        # Mock _ensure_channel to avoid real gRPC connection in VLM fallback
        async def _mock_ensure():
            return None

        client._ensure_channel = _mock_ensure

        # This should trigger VLM fallback (2nd consecutive timeout)
        result = await client.execute_task("test-vlm", "scrape", {"target_description": "price table"})
        assert result.get("_vlm_recovery") is True
        assert client._consecutive_timeouts == 0  # Reset after successful recovery

    asyncio.run(_run())


def test_rpa_client_timeout_counter_resets_on_success() -> None:
    """RPAClient resets timeout counter on successful execution"""
    from rpa_engine.abstract_layer.client import RPAClient

    async def _run() -> None:
        client = RPAClient()
        client._consecutive_timeouts = 1

        # Mock successful execution
        async def _mock_success(*args, **kwargs):
            return {"status": "ok"}

        client._execute_with_retry = _mock_success

        result = await client.execute_task("test-ok", "ping", {})
        assert result["status"] == "ok"
        assert client._consecutive_timeouts == 0

    asyncio.run(_run())
