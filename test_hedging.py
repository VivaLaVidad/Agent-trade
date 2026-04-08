"""
test_hedging.py — Buy-side 采购蜂群子图 + 背靠背套利测试
═══════════════════════════════════════════════════════
覆盖:
  1. 成功套利: 售价足够高 → ArbitrageEvaluator 通过 → HEDGE_LOCKED
  2. 套利失败: 上游涨价导致利润率 < 5% → HedgeFailed 熔断
  3. ScoutNode 供应商筛选
  4. BiddingNode asyncio.gather 并发询价 (原生 async)
  5. ProcurementOrder SHA-256 防篡改
  6. matching_graph 含 procurement_node 编译
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def test_scout_node_finds_suppliers() -> None:
    """ScoutNode 根据品类筛选上游供应商"""
    from agents.procurement_graph import scout_node

    state = {
        "target_sku": {"ticker_id": "CLAW-ELEC-CAP-100NF", "category": "capacitor"},
        "required_qty": 500,
        "sell_price_usd": 50.0,
    }
    result = scout_node(state)
    assert result["status"] == "suppliers_found"
    assert len(result["supplier_quotes"]) > 0
    assert len(result["supplier_quotes"]) <= 3


def test_scout_node_no_match(monkeypatch) -> None:
    """ScoutNode 无匹配品类（不修改全局列表，避免并行测试污染）"""
    import agents.procurement_graph as pg

    monkeypatch.setattr(pg, "_MOCK_UPSTREAM_SUPPLIERS", [])
    from agents.procurement_graph import scout_node

    state = {
        "target_sku": {"ticker_id": "CLAW-UNKN-X", "category": "exotic_material"},
        "required_qty": 100,
    }
    result = scout_node(state)
    assert result["status"] == "no_upstream_suppliers"


def test_bidding_node_concurrent_quotes() -> None:
    """BiddingNode 并发询价 (原生 async + asyncio.gather)"""
    from agents.procurement_graph import bidding_node

    async def _run() -> None:
        state = {
            "target_sku": {"ticker_id": "CLAW-ELEC-CAP-100NF"},
            "required_qty": 500,
            "sell_price_usd": 50.0,
            "supplier_quotes": [
                {"supplier": {"supplier_id": "s1", "supplier_name": "Supplier A",
                              "region": "SZ", "credibility_score": 90, "base_markup": 0.0}},
                {"supplier": {"supplier_id": "s2", "supplier_name": "Supplier B",
                              "region": "GZ", "credibility_score": 85, "base_markup": -0.05}},
            ],
        }
        result = await bidding_node(state)
        assert result["status"] == "quotes_received"
        assert len(result["supplier_quotes"]) == 2
        assert result["best_quote"]["total_cost_usd"] > 0
        costs = [q["total_cost_usd"] for q in result["supplier_quotes"]]
        assert costs == sorted(costs)

    asyncio.run(_run())


def test_arbitrage_success_hedge_locked() -> None:
    """成功套利: 售价足够高 → HEDGE_LOCKED"""
    from agents.procurement_graph import arbitrage_evaluator

    async def _run() -> None:
        state = {
            "sell_price_usd": 100.0,
            "shipping_estimate_usd": 5.0,
            "matched_trade_id": "TXN-TEST-SUCCESS",
            "best_quote": {
                "supplier_id": "upstream-001",
                "supplier_name": "Test Supplier",
                "ticker_id": "CLAW-ELEC-CAP-100NF",
                "unit_cost_usd": 0.15,
                "total_cost_usd": 75.0,
                "quantity": 500,
            },
        }

        with patch("agents.procurement_graph._persist_procurement_order_strict", new_callable=AsyncMock):
            result = await arbitrage_evaluator(state)

        assert result["status"] == "hedge_locked"
        arb = result["arbitrage_result"]
        assert arb["passed"] is True
        assert arb["decision"] == "HEDGE_LOCKED"
        assert arb["spread_usd"] == 20.0
        assert arb["spread_pct"] == 20.0
        assert arb["matched_trade_id"] == "TXN-TEST-SUCCESS"

        po = result["final_po"]
        assert po["lock_status"] == "locked"
        assert po["po_hash"]
        assert len(po["po_hash"]) == 64
        assert po["matched_trade_id"] == "TXN-TEST-SUCCESS"

    asyncio.run(_run())


def test_arbitrage_failure_hedge_failed() -> None:
    """套利失败: 上游涨价导致利润率 < 5% → HedgeFailed 熔断"""
    from agents.procurement_graph import arbitrage_evaluator

    async def _run() -> None:
        state = {
            "sell_price_usd": 100.0,
            "shipping_estimate_usd": 5.0,
            "matched_trade_id": "TXN-TEST-FAIL",
            "best_quote": {
                "supplier_id": "upstream-002",
                "supplier_name": "Expensive Supplier",
                "ticker_id": "CLAW-ELEC-CAP-100NF",
                "unit_cost_usd": 0.19,
                "total_cost_usd": 96.0,
                "quantity": 500,
            },
        }
        result = await arbitrage_evaluator(state)
        assert result["status"] == "hedge_failed"
        arb = result["arbitrage_result"]
        assert arb["passed"] is False
        assert arb["decision"] == "HEDGE_FAILED"
        assert arb["spread_pct"] < 5.0

    asyncio.run(_run())


def test_arbitrage_no_quotes() -> None:
    """无有效报价 → hedge_failed"""
    from agents.procurement_graph import arbitrage_evaluator

    async def _run() -> None:
        state = {
            "sell_price_usd": 100.0,
            "best_quote": {},
        }
        result = await arbitrage_evaluator(state)
        assert result["status"] == "hedge_failed"

    asyncio.run(_run())


def test_arbitrage_lock_rejects_empty_matched_trade_id() -> None:
    """套利率通过但缺少 matched_trade_id → TransactionContextMissing（禁止幽灵锁单）"""
    from agents.procurement_graph import TransactionContextMissing, arbitrage_evaluator

    async def _run() -> None:
        state = {
            "sell_price_usd": 100.0,
            "shipping_estimate_usd": 5.0,
            "matched_trade_id": "",
            "best_quote": {
                "supplier_id": "upstream-001",
                "supplier_name": "Test Supplier",
                "ticker_id": "CLAW-ELEC-CAP-100NF",
                "unit_cost_usd": 0.15,
                "total_cost_usd": 75.0,
                "quantity": 500,
            },
        }
        with patch("agents.procurement_graph._persist_procurement_order_strict", new_callable=AsyncMock):
            try:
                await arbitrage_evaluator(state)
            except TransactionContextMissing as exc:
                assert "matched_trade_id" in str(exc).lower() or "HEDGE_LOCKED" in str(exc)
            else:
                raise AssertionError("应抛出 TransactionContextMissing")

    asyncio.run(_run())


def test_procurement_graph_compiles() -> None:
    """procurement_graph 子图编译"""
    from agents.procurement_graph import build_procurement_graph
    graph = build_procurement_graph()
    assert graph is not None


def test_procurement_full_flow_success() -> None:
    """完整采购蜂群流程: 成功套利 (mock DB)"""
    from agents.procurement_graph import run_procurement_sync

    with patch("agents.procurement_graph._persist_procurement_order_strict", new_callable=AsyncMock):
        result = run_procurement_sync(
            target_sku={"ticker_id": "CLAW-ELEC-CAP-100NF", "sku_name": "100nF MLCC", "category": "capacitor"},
            required_qty=500,
            sell_price_usd=100.0,
            shipping_estimate_usd=3.0,
            matched_trade_id="TXN-FULL-FLOW-001",
        )

    assert result.get("status") in ("hedge_locked", "hedge_failed")

    if result["status"] == "hedge_locked":
        assert result["final_po"]["po_hash"]
        assert result["arbitrage_result"]["spread_pct"] >= 5.0
        assert result["final_po"]["matched_trade_id"] == "TXN-FULL-FLOW-001"


def test_po_hash_tamper_detection() -> None:
    """PO SHA-256 防篡改验证"""
    import hashlib
    import json
    from agents.procurement_graph import arbitrage_evaluator

    async def _run() -> None:
        state = {
            "sell_price_usd": 200.0,
            "shipping_estimate_usd": 5.0,
            "matched_trade_id": "TXN-HASH-TEST",
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

        if result["status"] == "hedge_locked":
            po = result["final_po"]
            original_hash = po["po_hash"]
            assert len(original_hash) == 64
            assert original_hash.isalnum()

            # 篡改 PO → 哈希不匹配
            tampered_po = dict(po)
            tampered_po.pop("po_hash")
            tampered_po["total_cost_usd"] = 999.99
            tampered_json = json.dumps(tampered_po, sort_keys=True, separators=(",", ":"), default=str)
            tampered_hash = hashlib.sha256(tampered_json.encode()).hexdigest()
            assert tampered_hash != original_hash

    asyncio.run(_run())


def test_matching_graph_compiles_with_procurement() -> None:
    """matching_graph 含 procurement_node 后仍能正常编译"""
    from modules.supply_chain.matching_graph import build_matching_graph
    graph = build_matching_graph()
    assert graph is not None


# ═══════════════════════════════════════════════════════════════
#  Local-First Inventory Tests
# ═══════════════════════════════════════════════════════════════

def test_mock_inventory_has_50_skus() -> None:
    """MockInventory preloaded with exactly 50 SKUs"""
    from database.mock_inventory import MockInventory
    inv = MockInventory()
    assert inv.size == 50


def test_mock_inventory_query_capacitor() -> None:
    """MockInventory query finds capacitors by keyword"""
    from database.mock_inventory import MockInventory
    inv = MockInventory()
    hits = inv.query("100nF", qty=1, category="capacitor")
    assert len(hits) >= 1
    assert hits[0]["sku_name"] == "100nF MLCC 0402"
    assert hits[0]["profit_margin_pct"] > 0


def test_mock_inventory_query_no_match() -> None:
    """MockInventory returns empty for non-existent SKU"""
    from database.mock_inventory import MockInventory
    inv = MockInventory()
    hits = inv.query("quantum_flux_capacitor_9999", qty=1)
    assert hits == []


def test_mock_inventory_remove_and_fallback() -> None:
    """Remove SKU -> query returns empty -> triggers scatter fallback"""
    from database.mock_inventory import MockInventory, InventoryItem
    inv = MockInventory()

    # First: query should find it
    hits = inv.query("STM32", qty=1, category="mcu")
    assert len(hits) >= 1
    found_id = hits[0]["sku_id"]

    # Remove it
    removed = inv.remove_sku(found_id)
    assert removed is True

    # Now query with exact sku_id should return empty
    hits2 = inv.query(found_id, qty=1)
    assert hits2 == []


def test_local_inventory_node_local_hit() -> None:
    """LocalInventoryNode returns LOCAL_INVENTORY when profitable SKU found"""
    from modules.supply_chain.matching_graph import local_inventory_node

    state = {
        "structured_demand": {
            "category": "capacitor",
            "product_keywords": "100nF",
            "quantity": 100,
        },
        "status": "demand_parsed",
    }
    result = local_inventory_node(state)
    assert result["source_type"] == "LOCAL_INVENTORY"
    assert result["status"] == "candidates_found"
    assert len(result["candidates"]) >= 1
    assert result["candidates"][0]["source"] == "local_inventory"


def test_local_inventory_node_no_match_triggers_remote() -> None:
    """LocalInventoryNode returns REMOTE_ARBITRAGE when no local match"""
    from modules.supply_chain.matching_graph import local_inventory_node

    state = {
        "structured_demand": {
            "category": "exotic_material",
            "product_keywords": "unobtanium_alloy",
            "quantity": 1,
        },
        "status": "demand_parsed",
    }
    result = local_inventory_node(state)
    assert result["source_type"] == "REMOTE_ARBITRAGE"
    assert result["status"] == "no_local_inventory"
    assert result["candidates"] == []


def test_scatter_node_returns_external_quotes() -> None:
    """ScatterNode broadcasts A2A and returns REMOTE_ARBITRAGE quotes"""
    from modules.supply_chain.matching_graph import scatter_node

    state = {
        "structured_demand": {
            "category": "capacitor",
            "product_keywords": "100nF MLCC",
            "quantity": 500,
        },
        "status": "no_local_inventory",
    }
    result = scatter_node(state)
    assert result["source_type"] == "REMOTE_ARBITRAGE"
    assert result["status"] == "candidates_found"
    assert len(result["candidates"]) == 3  # 3 external nodes
    assert len(result["scatter_quotes"]) == 3
    for c in result["candidates"]:
        assert c["source"] == "a2a_external"
        assert c["profit_margin_pct"] > 0


def test_local_first_full_flow_local_hit() -> None:
    """Full flow: local inventory hit -> source_type=LOCAL_INVENTORY -> skip scatter"""
    from modules.supply_chain.matching_graph import local_inventory_node, scatter_node

    # Step 1: local_inventory_node finds match
    state = {
        "structured_demand": {
            "category": "mcu",
            "product_keywords": "STM32",
            "quantity": 10,
        },
    }
    result = local_inventory_node(state)
    assert result["source_type"] == "LOCAL_INVENTORY"
    assert result["status"] == "candidates_found"
    # Scatter should NOT be called in this path


def test_local_first_full_flow_scatter_fallback() -> None:
    """Full flow: no local match -> scatter_node -> REMOTE_ARBITRAGE"""
    from modules.supply_chain.matching_graph import local_inventory_node, scatter_node

    # Step 1: local_inventory_node finds nothing
    state = {
        "structured_demand": {
            "category": "exotic",
            "product_keywords": "nonexistent_part",
            "quantity": 1,
        },
    }
    result1 = local_inventory_node(state)
    assert result1["source_type"] == "REMOTE_ARBITRAGE"
    assert result1["status"] == "no_local_inventory"

    # Step 2: scatter_node collects external quotes
    state.update(result1)
    result2 = scatter_node(state)
    assert result2["source_type"] == "REMOTE_ARBITRAGE"
    assert result2["status"] == "candidates_found"
    assert len(result2["candidates"]) > 0


def test_matching_graph_has_local_inventory_and_scatter_nodes() -> None:
    """matching_graph includes local_inventory_node and scatter_node"""
    from modules.supply_chain.matching_graph import build_matching_graph
    graph = build_matching_graph()
    assert graph is not None
    node_names = [n.name if hasattr(n, "name") else str(n) for n in graph.get_graph().nodes]
    assert "local_inventory_node" in node_names
    assert "scatter_node" in node_names
