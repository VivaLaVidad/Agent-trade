"""
test_integration_smoke.py — Roo 架构增补模块的快速冒烟测试（无 Ollama）
──────────────────────────────────────────────────────────────────────
覆盖：TickPricing、ComplianceGateway、IdempotencyGuard（内存路径）、图编译。
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def test_tick_pricing_audit_trail() -> None:
    from modules.supply_chain.tick_pricing import TickPricingEngine

    eng = TickPricingEngine()
    r = eng.compute_tick(
        base_price_rmb=0.5,
        stock_qty=10_000,
        moq=100,
        demand_qty=500,
    )
    assert "adjusted_price_rmb" in r
    assert "tick_score" in r
    trail = r.get("pricing_audit_trail") or {}
    assert trail.get("signature")
    assert len(trail["signature"]) == 64


def test_compliance_gateway_sanitize() -> None:
    from modules.audit_module.compliance_gateway import ComplianceGateway

    g = ComplianceGateway()
    out = g.sanitize(
        {
            "buyer_email": "buyer@example.com",
            "unit_price_rmb": 1.23,
            "product_line": "MLCC capacitors",
        },
    )
    assert out.get("_compliance", {}).get("sanitized") is True
    assert out.get("product_line") == "MLCC capacitors"
    assert out.get("unit_price_rmb") == "***"
    assert "*" in str(out.get("buyer_email", ""))


def test_idempotency_guard_memory() -> None:
    from core.security import IdempotencyGuard

    async def _run() -> None:
        g = IdempotencyGuard(ttl_seconds=3600)
        tid = "smoke-test-trade-001"
        assert await g.check_and_acquire(tid) is True
        assert await g.check_and_acquire(tid) is False
        await g.release(tid)
        assert await g.check_and_acquire(tid) is True

    asyncio.run(_run())


def test_graphs_compile_with_checkpointer() -> None:
    from agents.workflow_graph import build_trade_graph
    from modules.supply_chain.matching_graph import build_matching_graph

    assert build_trade_graph() is not None
    assert build_matching_graph() is not None
