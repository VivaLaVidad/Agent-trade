"""
test_ticker_plant.py — Ticker Plant + MarketDataBus + 波动率中断 冒烟测试
═══════════════════════════════════════════════════════════════════════════
覆盖:
  1. TickerRegistry 解析 + 缓存
  2. MarketDataBus 进程内广播
  3. TickPricingEngine 事件驱动 + Ticker 绑定
  4. NegotiatorAgent 波动率突变硬中断 (核心测试)
  5. LedgerService Ticker 签名
  6. 审计追踪签名验证 (含 ticker_id)
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def test_ticker_registry_resolve() -> None:
    """TickerRegistry 将非标品类解析为标准 Ticker ID"""
    from core.ticker_plant import TickerRegistry

    reg = TickerRegistry()

    # 标准品类
    t1 = reg.resolve("capacitor", "100nF 50V 0805 MLCC")
    assert t1.ticker_id.startswith("CLAW-ELEC-CAP-")
    assert "100NF" in t1.ticker_id or "50V" in t1.ticker_id

    # 非标别名
    t2 = reg.resolve("MLCC", "100nF 50V 0805")
    assert t2.ticker_id.startswith("CLAW-ELEC-CAP-")

    # 中文别名
    t3 = reg.resolve("贴片电容", "100nF 50V")
    assert t3.ticker_id.startswith("CLAW-ELEC-CAP-")

    # 缓存一致性
    t4 = reg.resolve("capacitor", "100nF 50V 0805 MLCC")
    assert t4.ticker_id == t1.ticker_id

    # 搜索
    results = reg.search("CAP")
    assert len(results) > 0


def test_ticker_registry_different_specs() -> None:
    """不同规格的同品类应生成不同 Ticker"""
    from core.ticker_plant import TickerRegistry

    reg = TickerRegistry()
    t1 = reg.resolve("resistor", "10K 0603 1%")
    t2 = reg.resolve("resistor", "100K 0805 5%")
    assert t1.ticker_id != t2.ticker_id
    assert t1.ticker_id.startswith("CLAW-ELEC-RES-")
    assert t2.ticker_id.startswith("CLAW-ELEC-RES-")


def test_market_data_bus_local_broadcast() -> None:
    """MarketDataBus 进程内广播 + 订阅"""
    from core.ticker_plant import MarketDataBus, MarketEvent, EventType

    async def _run() -> None:
        bus = MarketDataBus()
        received: list[MarketEvent] = []

        async def handler(event: MarketEvent) -> None:
            received.append(event)

        bus.subscribe("CLAW-ELEC-CAP-TEST", handler)

        event = MarketEvent(
            event_type=EventType.PRICE_UPDATE,
            ticker_id="CLAW-ELEC-CAP-TEST",
            data={"new_price_rmb": 0.52},
        )
        await bus.publish(event)

        # 等待异步分发
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].ticker_id == "CLAW-ELEC-CAP-TEST"
        assert received[0].data["new_price_rmb"] == 0.52

    asyncio.run(_run())


def test_market_data_bus_wildcard() -> None:
    """MarketDataBus 通配符订阅"""
    from core.ticker_plant import MarketDataBus, MarketEvent, EventType

    async def _run() -> None:
        bus = MarketDataBus()
        received: list[MarketEvent] = []

        async def handler(event: MarketEvent) -> None:
            received.append(event)

        bus.subscribe("CLAW-ELEC-*", handler)

        await bus.publish(MarketEvent(
            event_type=EventType.PRICE_UPDATE,
            ticker_id="CLAW-ELEC-CAP-100NF",
            data={},
        ))
        await bus.publish(MarketEvent(
            event_type=EventType.PRICE_UPDATE,
            ticker_id="CLAW-ELEC-RES-10K",
            data={},
        ))
        await bus.publish(MarketEvent(
            event_type=EventType.PRICE_UPDATE,
            ticker_id="CLAW-MECH-MOTOR-X1",
            data={},
        ))

        await asyncio.sleep(0.1)

        # 只有 CLAW-ELEC-* 匹配的两个事件
        assert len(received) == 2

    asyncio.run(_run())


def test_tick_pricing_with_ticker() -> None:
    """TickPricingEngine 事件驱动 + Ticker 绑定"""
    from modules.supply_chain.tick_pricing import TickPricingEngine

    eng = TickPricingEngine()
    r = eng.compute_tick(
        base_price_rmb=0.5,
        stock_qty=10_000,
        moq=100,
        demand_qty=500,
        ticker_id="CLAW-ELEC-CAP-100NF50V",
    )
    assert r["ticker_id"] == "CLAW-ELEC-CAP-100NF50V"
    assert "adjusted_price_rmb" in r
    assert "tick_score" in r
    assert "is_volatility_spike" in r

    trail = r.get("pricing_audit_trail") or {}
    assert trail.get("ticker_id") == "CLAW-ELEC-CAP-100NF50V"
    assert trail.get("signature")
    assert len(trail["signature"]) == 64


def test_tick_pricing_auto_resolve_ticker() -> None:
    """TickPricingEngine 自动解析 Ticker (无显式 ticker_id)"""
    from modules.supply_chain.tick_pricing import TickPricingEngine

    eng = TickPricingEngine()
    r = eng.compute_tick(
        base_price_rmb=1.0,
        stock_qty=5000,
        moq=200,
        demand_qty=1000,
        category="resistor",
        sku_name="10K 0603 1% Thin Film",
    )
    assert r["ticker_id"].startswith("CLAW-ELEC-RES-")


def test_tick_pricing_audit_trail_signature_with_ticker() -> None:
    """审计追踪签名验证 (含 ticker_id)"""
    from modules.supply_chain.tick_pricing import TickPricingEngine

    eng = TickPricingEngine()
    r = eng.compute_tick(
        base_price_rmb=0.5,
        stock_qty=10_000,
        moq=100,
        demand_qty=500,
        ticker_id="CLAW-ELEC-CAP-TEST",
    )
    trail = r["pricing_audit_trail"]
    assert TickPricingEngine.verify_audit_trail(trail) is True

    # 篡改测试
    tampered = dict(trail)
    tampered["adjusted_price_rmb"] = 999.99
    assert TickPricingEngine.verify_audit_trail(tampered) is False


def test_ledger_ticker_signature() -> None:
    """LedgerService 含 Ticker ID 的签名"""
    from modules.supply_chain.ledger import LedgerService

    ledger = LedgerService(fee_rate=0.01)
    txn = ledger.create_transaction(
        merchant_id="merchant-test-001",
        client_id="client-test-001",
        amount_usd=100.0,
        ticker_id="CLAW-ELEC-CAP-100NF50V",
    )
    assert txn["ticker_id"] == "CLAW-ELEC-CAP-100NF50V"
    assert txn["signature"]
    assert ledger.verify_signature(txn) is True

    # 篡改测试
    tampered = dict(txn)
    tampered["amount_usd"] = 999.99
    assert ledger.verify_signature(tampered) is False


def test_negotiator_volatility_interrupt() -> None:
    """核心测试: 首个 SKU 获批后、处理下一候选前注入 VOLATILITY_SPIKE，
    验证事件循环让出后 Negotiator 能硬中断并重算（不依赖 wall-clock 竞态）。

    流程:
    1. patched _evaluate_candidate 在首次 approved 时 set Event
    2. inject 协程 wait Event 后 publish spike（发生在第二轮 await sleep(0) 之前）
    3. 断言 volatility_interrupted、INTERRUPT / REPRICE 日志与结构字段
    """
    from core.ticker_plant import MarketDataBus, MarketEvent, EventType, TickerRegistry
    from modules.supply_chain.negotiator import NegotiatorAgent

    async def _run() -> None:
        bus = MarketDataBus()
        registry = TickerRegistry()

        import core.ticker_plant as tp

        old_bus = tp._market_bus
        old_reg = tp._ticker_registry
        tp._market_bus = bus
        tp._ticker_registry = registry

        first_approved = asyncio.Event()
        _orig_eval = NegotiatorAgent._evaluate_candidate

        def _patched_eval(
            self: NegotiatorAgent,
            cand: dict,
            quantity: int,
            budget_usd: float,
            certs_req: list,
            destination: str,
            log: list,
        ) -> dict:
            r = _orig_eval(
                self, cand, quantity, budget_usd, certs_req, destination, log,
            )
            if r.get("status") == "approved":
                first_approved.set()
            return r

        NegotiatorAgent._evaluate_candidate = _patched_eval  # type: ignore[method-assign]

        try:
            negotiator = NegotiatorAgent()

            demand = {
                "quantity": 500,
                "budget_usd": 200.0,
                "certs_required": ["CE"],
                "destination": "Nigeria",
            }

            candidates = [
                {
                    "sku_id": "sku-001",
                    "sku_name": "100nF 50V 0805 MLCC",
                    "category": "capacitor",
                    "supplier_name": "Shenzhen Electronics",
                    "unit_price_rmb": 0.5,
                    "moq": 100,
                    "stock_qty": 10000,
                    "certifications": ["CE", "RoHS"],
                    "match_score": 0.95,
                    "specs": {"voltage": "50V", "package": "0805"},
                },
                {
                    "sku_id": "sku-002",
                    "sku_name": "100nF 25V 0603 MLCC",
                    "category": "capacitor",
                    "supplier_name": "Guangzhou Parts",
                    "unit_price_rmb": 0.3,
                    "moq": 200,
                    "stock_qty": 5000,
                    "certifications": ["CE"],
                    "match_score": 0.85,
                    "specs": {"voltage": "25V", "package": "0603"},
                },
            ]

            async def inject_spike() -> None:
                await first_approved.wait()
                tickers = registry.all_tickers()
                target_tid = tickers[0].ticker_id if tickers else "CLAW-ELEC-CAP-100NF50V"
                spike = MarketEvent(
                    event_type=EventType.VOLATILITY_SPIKE,
                    ticker_id=target_tid,
                    data={
                        "volatility_7d": 0.18,
                        "fx_drift": -0.8,
                        "fx_rate_mid": 7.45,
                        "threshold": 0.12,
                        "severity": "high",
                    },
                )
                await bus.publish(spike)

            result, _ = await asyncio.gather(
                negotiator.execute(None, demand, candidates),
                inject_spike(),
            )

            assert result["volatility_interrupted"] is True
            neg_log = result.get("negotiation_log", [])
            assert len(neg_log) > 0

            interrupt_logs = [ln for ln in neg_log if "[INTERRUPT]" in ln]
            assert len(interrupt_logs) > 0

            assert result.get("all_approved")
            reprice_logs = [ln for ln in neg_log if "[REPRICE]" in ln]
            assert len(reprice_logs) > 0

            assert "best_match" in result
            assert "all_approved" in result
            assert "alternatives" in result
            assert "bundling_suggestions" in result
            assert "tiered_quotes" in result

            for cand in candidates:
                assert "ticker_id" in cand
                assert cand["ticker_id"].startswith("CLAW-")

        finally:
            NegotiatorAgent._evaluate_candidate = _orig_eval  # type: ignore[method-assign]
            tp._market_bus = old_bus
            tp._ticker_registry = old_reg

    asyncio.run(_run())
