"""
test_a2a_askb.py — A2A Protocol + ASKB Trader Copilot Tests
═══════════════════════════════════════════════════════════
覆盖:
  1. A2APayload 残缺字段触发 ValidationError
  2. scatter_node A2A 并发处理 3 个合法 payload
  3. ASKB 路由解析意图并调用 Inventory Tool
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def test_a2a_payload_missing_txn_id_raises() -> None:
    """A2APayload 缺失 sell_side_transaction_id → ValidationError"""
    from pydantic import ValidationError
    from models.a2a_protocol import A2APayload, AgentCard, TurnStatus

    try:
        A2APayload(
            agent_card=AgentCard(agent_id="test-01", capabilities=["quote"], endpoint="a2a://test"),
            negotiation_round=0,
            turn_status=TurnStatus.OFFER,
            proposed_price=1.5,
            moq=100,
            sell_side_transaction_id="",  # empty → should fail
        )
        raise AssertionError("Should have raised ValidationError")
    except ValidationError as exc:
        assert "sell_side_transaction_id" in str(exc).lower()


def test_a2a_payload_valid_construction() -> None:
    """A2APayload 合法构造 + 字段校验"""
    from decimal import Decimal
    from models.a2a_protocol import A2APayload, AgentCard, TurnStatus

    payload = A2APayload(
        agent_card=AgentCard(agent_id="ext-sz-01", capabilities=["quote", "hedge"], endpoint="a2a://sz.claw"),
        negotiation_round=1,
        turn_status=TurnStatus.COUNTER_OFFER,
        proposed_price=Decimal("0.1234"),
        moq=500,
        sell_side_transaction_id="TXN-A2A-TEST-001",
        sku_name="100nF MLCC",
        available_qty=10000,
        profit_margin_pct=8.5,
    )
    assert payload.agent_card.agent_id == "ext-sz-01"
    assert payload.sell_side_transaction_id == "TXN-A2A-TEST-001"
    assert payload.turn_status == TurnStatus.COUNTER_OFFER
    assert float(payload.proposed_price) == 0.1234


def test_scatter_node_a2a_concurrent_3_payloads() -> None:
    """scatter_node 并发处理 3 个合法 A2A payload，耗时 < 5s"""
    import time
    from modules.supply_chain.matching_graph import scatter_node

    state = {
        "structured_demand": {
            "category": "capacitor",
            "product_keywords": "100nF MLCC",
            "quantity": 500,
        },
        "sell_side_transaction_id": "TXN-SCATTER-A2A-001",
        "status": "no_local_inventory",
    }

    start = time.monotonic()
    result = scatter_node(state)
    elapsed = time.monotonic() - start

    assert result["source_type"] == "REMOTE_ARBITRAGE"
    assert result["status"] == "candidates_found"
    assert len(result["candidates"]) == 3
    assert len(result["scatter_quotes"]) == 3
    assert elapsed < 5.0, f"scatter_node took {elapsed:.2f}s (should be < 5s)"

    # Verify A2A fields in scatter_quotes
    for q in result["scatter_quotes"]:
        assert "sell_side_transaction_id" in q
        assert q["sell_side_transaction_id"] == "TXN-SCATTER-A2A-001"
        assert "turn_status" in q
        assert q["turn_status"] == "OFFER"
        assert "negotiation_round" in q


def test_askb_copilot_inventory_query() -> None:
    """ASKB 解析库存利润查询意图并调用 Inventory Tool"""
    from modules.agents.askb_agent import ASKBTraderCopilot

    copilot = ASKBTraderCopilot()

    async def _run() -> None:
        result = await copilot.process("分析 100nF 电容的本地库存利润率")
        assert result["status"] == "success"
        assert result["intent"] == "inventory_profit_analysis"
        assert result["tool_used"] == "query_inventory_profit"
        assert result["data"]["found"] is True
        assert result["data"]["match_count"] >= 1
        assert result["data"]["best_margin_pct"] > 0
        assert "建议" in result["recommendation"] or "LOCAL" in result["recommendation"]

    asyncio.run(_run())


def test_askb_copilot_market_query() -> None:
    """ASKB 解析市场底价查询意图"""
    from modules.agents.askb_agent import ASKBTraderCopilot

    copilot = ASKBTraderCopilot()

    async def _run() -> None:
        result = await copilot.process("查询 CLAW-ELEC-CAP-100NF 的市场底价和套利空间")
        assert result["status"] == "success"
        assert result["intent"] == "market_price_query"
        assert result["tool_used"] == "query_market_bus"
        # MarketDataBus may not have events (no Redis), but tool should still return
        assert "ticker_query" in result["data"]

    asyncio.run(_run())


def test_askb_copilot_unknown_intent() -> None:
    """ASKB 无法识别意图 → unrecognized"""
    from modules.agents.askb_agent import ASKBTraderCopilot

    copilot = ASKBTraderCopilot()

    async def _run() -> None:
        result = await copilot.process("今天天气怎么样")
        assert result["status"] == "unrecognized"
        assert result["intent"] == "unknown"
        assert result["tool_used"] is None

    asyncio.run(_run())



def test_flash_intent_local_match_with_trust_anchors() -> None:
    """flash-intent finds local SKU with UN/RCEP trust anchors"""
    from database.mock_inventory import get_mock_inventory

    inventory = get_mock_inventory()
    hits = inventory.query("100nF", qty=1, category="capacitor")
    assert len(hits) >= 1
    best = hits[0]

    # Verify trust anchor fields exist and are True
    assert best["is_un_certified"] is True
    assert best["is_rcep_eligible"] is True

    # Simulate flash-intent logic
    delivery = "24 Hours (in-stock)" if best["stock_qty"] >= 1 else "2-3 Business Days"
    assert delivery == "24 Hours (in-stock)"
    assert best["profit_margin_pct"] > 5.0


def test_flash_intent_no_match_returns_remote() -> None:
    """flash-intent with non-existent SKU returns REMOTE_ARBITRAGE"""
    from database.mock_inventory import get_mock_inventory

    inventory = get_mock_inventory()
    hits = inventory.query("quantum_flux_9999", qty=1)
    assert hits == []
    # In the actual route, this would return source_type=REMOTE_ARBITRAGE
    source_type = "REMOTE_ARBITRAGE" if not hits else "LOCAL_INVENTORY"
    assert source_type == "REMOTE_ARBITRAGE"
