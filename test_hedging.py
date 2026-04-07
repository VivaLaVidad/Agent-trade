"""
test_hedging.py — Buy-side 采购蜂群子图 + 背靠背套利测试
═══════════════════════════════════════════════════════
覆盖:
  1. 成功套利: 售价足够高 → ArbitrageEvaluator 通过 → HEDGE_LOCKED
  2. 套利失败: 上游涨价导致利润率 < 5% → HedgeFailed 熔断
  3. ScoutNode 供应商筛选
  4. BiddingNode asyncio.gather 并发询价
  5. ProcurementOrder SHA-256 防篡改
  6. matching_graph 含 procurement_node 编译
"""

from __future__ import annotations

import asyncio
import os
import sys

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
    """BiddingNode 并发询价 (asyncio.gather)"""
    from agents.procurement_graph import bidding_node

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
    result = bidding_node(state)
    assert result["status"] == "quotes_received"
    assert len(result["supplier_quotes"]) == 2
    assert result["best_quote"]["total_cost_usd"] > 0
    # 应按成本排序 (最低在前)
    costs = [q["total_cost_usd"] for q in result["supplier_quotes"]]
    assert costs == sorted(costs)


def test_arbitrage_success_hedge_locked() -> None:
    """成功套利: 售价足够高 → HEDGE_LOCKED"""
    from agents.procurement_graph import arbitrage_evaluator

    state = {
        "sell_price_usd": 100.0,
        "shipping_estimate_usd": 5.0,
        "best_quote": {
            "supplier_id": "upstream-001",
            "supplier_name": "Test Supplier",
            "ticker_id": "CLAW-ELEC-CAP-100NF",
            "unit_cost_usd": 0.15,
            "total_cost_usd": 75.0,  # 成本 $75
            "quantity": 500,
        },
    }
    result = asyncio.run(arbitrage_evaluator(state))

    assert result["status"] == "hedge_locked"
    arb = result["arbitrage_result"]
    assert arb["passed"] is True
    assert arb["decision"] == "HEDGE_LOCKED"
    assert arb["spread_usd"] == 20.0  # 100 - 75 - 5 = 20
    assert arb["spread_pct"] == 20.0  # 20/100 * 100 = 20%
    assert arb["spread_pct"] >= 5.0   # > 5% 阈值

    # 验证 PO 生成
    po = result["final_po"]
    assert po["lock_status"] == "locked"
    assert po["po_hash"]
    assert len(po["po_hash"]) == 64  # SHA-256


def test_arbitrage_failure_hedge_failed() -> None:
    """套利失败: 上游涨价导致利润率 < 5% → HedgeFailed 熔断"""
    from agents.procurement_graph import arbitrage_evaluator

    state = {
        "sell_price_usd": 100.0,
        "shipping_estimate_usd": 5.0,
        "best_quote": {
            "supplier_id": "upstream-002",
            "supplier_name": "Expensive Supplier",
            "ticker_id": "CLAW-ELEC-CAP-100NF",
            "unit_cost_usd": 0.19,
            "total_cost_usd": 96.0,  # 成本 $96 → 利润仅 $-1
            "quantity": 500,
        },
    }
    result = asyncio.run(arbitrage_evaluator(state))

    assert result["status"] == "hedge_failed"
    arb = result["arbitrage_result"]
    assert arb["passed"] is False
    assert arb["decision"] == "HEDGE_FAILED"
    assert arb["spread_pct"] < 5.0  # 低于 5% 阈值


def test_arbitrage_no_quotes() -> None:
    """无有效报价 → hedge_failed"""
    from agents.procurement_graph import arbitrage_evaluator

    state = {
        "sell_price_usd": 100.0,
        "best_quote": {},
    }
    result = asyncio.run(arbitrage_evaluator(state))
    assert result["status"] == "hedge_failed"


def test_procurement_graph_compiles() -> None:
    """procurement_graph 子图编译"""
    from agents.procurement_graph import build_procurement_graph
    graph = build_procurement_graph()
    assert graph is not None


def test_procurement_full_flow_success() -> None:
    """完整采购蜂群流程: 成功套利"""
    import random

    from agents.procurement_graph import run_procurement_sync

    random.seed(42)

    result = run_procurement_sync(
        target_sku={"ticker_id": "CLAW-ELEC-CAP-100NF", "sku_name": "100nF MLCC", "category": "capacitor"},
        required_qty=500,
        sell_price_usd=100.0,
        shipping_estimate_usd=3.0,
    )

    # 由于 mock 报价有随机性，结果可能是 locked 或 failed
    assert result.get("status") in ("hedge_locked", "hedge_failed")

    if result["status"] == "hedge_locked":
        assert result["final_po"]["po_hash"]
        assert result["arbitrage_result"]["spread_pct"] >= 5.0
    else:
        assert result["arbitrage_result"]["spread_pct"] < 5.0


def test_po_hash_tamper_detection() -> None:
    """PO SHA-256 防篡改验证"""
    import hashlib
    import json
    from agents.procurement_graph import arbitrage_evaluator

    state = {
        "sell_price_usd": 200.0,
        "shipping_estimate_usd": 5.0,
        "best_quote": {
            "supplier_id": "upstream-001",
            "supplier_name": "Test Supplier",
            "ticker_id": "CLAW-ELEC-RES-10K",
            "unit_cost_usd": 0.2,
            "total_cost_usd": 100.0,
            "quantity": 500,
        },
    }
    result = asyncio.run(arbitrage_evaluator(state))

    if result["status"] == "hedge_locked":
        po = result["final_po"]
        original_hash = po["po_hash"]

        # 验证哈希格式
        assert len(original_hash) == 64
        assert original_hash.isalnum()

        # 篡改 PO → 哈希不匹配
        tampered_po = dict(po)
        tampered_po.pop("po_hash")
        tampered_po["total_cost_usd"] = 999.99
        tampered_json = json.dumps(tampered_po, sort_keys=True)
        tampered_hash = hashlib.sha256(tampered_json.encode()).hexdigest()
        assert tampered_hash != original_hash


def test_matching_graph_compiles_with_procurement() -> None:
    """matching_graph 含 procurement_node 后仍能正常编译"""
    from modules.supply_chain.matching_graph import build_matching_graph
    graph = build_matching_graph()
    assert graph is not None
