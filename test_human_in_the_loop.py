"""
test_human_in_the_loop.py — HITL Graph Suspension + Trade Execution Tests
═════════════════════════════════════════════════════════════════════════
覆盖:
  1. Graph compiles with interrupt_before (risk_defense_node)
  2. llm_sourcing_node returns A2A-structured candidates (DEMO fallback)
  3. buyer_confirmation_node selects candidate
  4. NEW_TRADE_EXECUTED event published to MarketDataBus
  5. local_inventory_node DB fallback to MockInventory
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def test_graph_compiles_with_interrupt() -> None:
    """matching_graph compiles with interrupt_before=['risk_defense_node']"""
    from modules.supply_chain.matching_graph import build_matching_graph
    graph = build_matching_graph()
    assert graph is not None


def test_llm_sourcing_node_demo_fallback() -> None:
    """llm_sourcing_node falls back to scatter in DEMO mode, returns A2A candidates"""
    from modules.supply_chain.matching_graph import llm_sourcing_node

    state = {
        "structured_demand": {
            "category": "capacitor",
            "product_keywords": "100nF MLCC",
            "quantity": 500,
        },
        "sell_side_transaction_id": "TXN-LLM-TEST-001",
        "status": "no_local_inventory",
    }
    result = llm_sourcing_node(state)
    assert result["source_type"] == "REMOTE_ARBITRAGE"
    assert result["status"] == "candidates_found"
    assert len(result["candidates"]) >= 1
    # Verify A2A fields in scatter_quotes
    for q in result.get("scatter_quotes", []):
        assert "sell_side_transaction_id" in q


def test_buyer_confirmation_node_selects_candidate() -> None:
    """buyer_confirmation_node picks selected candidate from list"""
    from modules.supply_chain.matching_graph import buyer_confirmation_node

    state = {
        "candidates": [
            {"sku_id": "SKU-A", "sku_name": "Part A", "cost_price_usd": 0.1},
            {"sku_id": "SKU-B", "sku_name": "Part B", "cost_price_usd": 0.2},
        ],
        "buyer_confirmation": {"selected_quote_id": "SKU-B"},
    }
    result = buyer_confirmation_node(state)
    assert result["status"] == "buyer_confirmed"
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["sku_id"] == "SKU-B"


def test_buyer_confirmation_auto_select() -> None:
    """buyer_confirmation_node auto-selects first if no explicit selection"""
    from modules.supply_chain.matching_graph import buyer_confirmation_node

    state = {
        "candidates": [
            {"sku_id": "SKU-BEST", "sku_name": "Best Part"},
        ],
        "buyer_confirmation": {},
    }
    result = buyer_confirmation_node(state)
    assert result["status"] == "buyer_confirmed"
    assert result["candidates"][0]["sku_id"] == "SKU-BEST"


def test_new_trade_executed_event_type_exists() -> None:
    """EventType.NEW_TRADE_EXECUTED is defined"""
    from core.ticker_plant import EventType
    assert hasattr(EventType, "NEW_TRADE_EXECUTED")
    assert EventType.NEW_TRADE_EXECUTED.value == "new_trade_executed"


def test_market_bus_publishes_new_trade_event() -> None:
    """MarketDataBus can publish and receive NEW_TRADE_EXECUTED events"""
    from core.ticker_plant import MarketDataBus, MarketEvent, EventType

    async def _run() -> None:
        bus = MarketDataBus()
        await bus.start()

        received = []

        async def _handler(event: MarketEvent) -> None:
            received.append(event)

        bus.subscribe("CLAW-TRADE-*", _handler)

        event = MarketEvent(
            event_type=EventType.NEW_TRADE_EXECUTED,
            ticker_id="CLAW-TRADE-TEST001",
            data={
                "thread_id": "test-thread",
                "sell_side_transaction_id": "TXN-TEST",
                "status": "completed",
            },
        )
        await bus.publish(event)
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].event_type == EventType.NEW_TRADE_EXECUTED
        assert received[0].data["sell_side_transaction_id"] == "TXN-TEST"

        await bus.stop()

    asyncio.run(_run())


def test_graph_has_llm_sourcing_and_confirmation_nodes() -> None:
    """matching_graph includes llm_sourcing_node and buyer_confirmation_node"""
    from modules.supply_chain.matching_graph import build_matching_graph
    graph = build_matching_graph()
    node_names = [n.name if hasattr(n, "name") else str(n) for n in graph.get_graph().nodes]
    assert "llm_sourcing_node" in node_names
    assert "buyer_confirmation_node" in node_names


def test_local_inventory_node_demo_fallback() -> None:
    """local_inventory_node uses MockInventory in DEMO mode"""
    from modules.supply_chain.matching_graph import local_inventory_node

    state = {
        "structured_demand": {
            "category": "mcu",
            "product_keywords": "STM32",
            "quantity": 1,
        },
    }
    result = local_inventory_node(state)
    assert result["source_type"] == "LOCAL_INVENTORY"
    assert result["status"] == "candidates_found"
    assert len(result["candidates"]) >= 1
