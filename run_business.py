"""
run_business.py — Project Claw 高并发撮合业务入口
──────────────────────────────────────────────────
职责：
  1. 多租户撮合：每个请求携带 merchant_id + client_id
  2. asyncio.gather 并发处理多个买家询盘
  3. 撮合成交后自动调用 LedgerService 生成签名流水
  4. 返回结构化结果（含交易流水 + 路由费）
"""

import asyncio
import os
import sys
import json
import uuid
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from dotenv import load_dotenv
load_dotenv()

from core.logger import get_logger
from modules.supply_chain.demand_agent import DemandAgent
from modules.supply_chain.supply_agent import SupplyAgent
from modules.supply_chain.negotiator import NegotiatorAgent
from modules.supply_chain.ledger import LedgerService

logger = get_logger(__name__)


async def match_single(
    raw_input: str,
    merchant_id: str,
    client_id: str,
    ledger: LedgerService,
) -> dict[str, Any]:
    """处理单个买家询盘的完整撮合流程"""
    tag = f"[{merchant_id[:6]}:{client_id[:6]}]"

    demand_agent = DemandAgent()
    demand = await demand_agent.execute(None, {"raw_input": raw_input})
    if not demand.get("valid"):
        return {"merchant_id": merchant_id, "status": "demand_invalid", "error": demand.get("error")}

    logger.info("%s 需求解析: %s qty=%d $%s",
                tag, demand.get("category"), demand.get("quantity",0), demand.get("budget_usd",0))

    supply_agent = SupplyAgent()
    candidates = await supply_agent.execute(None, {
        "category": demand.get("category", ""),
        "specs": demand.get("specs", {}),
        "certs_required": demand.get("certs_required", []),
        "budget_usd": demand.get("budget_usd", 0),
        "quantity": demand.get("quantity", 0),
        "merchant_id": merchant_id,
        "top_n": 5,
    })

    if not candidates:
        return {"merchant_id": merchant_id, "status": "no_candidates"}

    from agents.agent_workflow import apply_risk_defense_to_candidates
    apply_risk_defense_to_candidates(candidates)

    negotiator = NegotiatorAgent()
    neg = await negotiator.execute(None, demand, candidates)

    best = neg.get("best_match")
    if not best or best.get("status") != "approved":
        return {
            "merchant_id": merchant_id,
            "status": "no_approval",
            "bundling": len(neg.get("bundling_suggestions", [])),
            "alternatives": len(neg.get("alternatives", [])),
            "tiered_quotes": neg.get("tiered_quotes", []),
            "log": neg.get("negotiation_log", []),
        }

    amount = best.get("landed_usd", 0)
    txn = ledger.create_transaction(
        merchant_id=merchant_id,
        client_id=client_id,
        amount_usd=amount,
        match_id=best.get("sku_id", ""),
        po_number=f"PO-{uuid.uuid4().hex[:8].upper()}",
    )
    await ledger.persist(txn)

    return {
        "merchant_id": merchant_id,
        "client_id": client_id,
        "status": "settled",
        "sku": best.get("sku_name"),
        "supplier": best.get("supplier_name"),
        "amount_usd": amount,
        "routing_fee_usd": txn["routing_fee_usd"],
        "transaction_id": txn["transaction_id"],
        "signature": txn["signature"][:16] + "...",
        "tiered_quotes": neg.get("tiered_quotes", []),
        "log": neg.get("negotiation_log", []),
    }


_DEMO_REQUESTS = [
    {
        "merchant_id": "merchant-alpha-001",
        "client_id": "client-egypt-ahmed",
        "raw_input": "Need 300pcs 100nF 50V ceramic capacitors SMD-0805. CE cert required. Budget $150 CIF Cairo.",
    },
    {
        "merchant_id": "merchant-alpha-001",
        "client_id": "client-nigeria-oke",
        "raw_input": "We need 200 units white LED SMD-2835 for lighting project. Budget $100. Ship to Lagos Nigeria.",
    },
    {
        "merchant_id": "merchant-beta-002",
        "client_id": "client-india-raj",
        "raw_input": "Require 1000pcs 10K resistors 0603 package 1% tolerance. RoHS needed. Budget $50 FOB to Mumbai.",
    },
]


async def main() -> None:
    import modules.supply_chain.models  # noqa
    from database.models import Base, async_engine

    print("=" * 70)
    print("  Project Claw — 多租户高并发撮合演示")
    print("=" * 70)

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from modules.supply_chain.mock_data import generate_mock_catalog
    stats = await generate_mock_catalog(num_suppliers=30, num_skus=150)
    print(f"\n[初始化] {stats['suppliers']}供应商 / {stats['skus']}SKU\n")

    ledger = LedgerService(fee_rate=0.01)

    print(f"[并发撮合] 同时处理 {len(_DEMO_REQUESTS)} 个买家请求...\n")

    tasks = [
        match_single(r["raw_input"], r["merchant_id"], r["client_id"], ledger)
        for r in _DEMO_REQUESTS
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    print("-" * 70)
    for i, result in enumerate(results, 1):
        if isinstance(result, Exception):
            print(f"  #{i} [异常] {result}")
            continue

        status = result.get("status", "?")
        merchant = result.get("merchant_id", "?")[:12]

        if status == "settled":
            print(f"  #{i} [{merchant}] 成交  "
                  f"SKU={result.get('sku','')}  "
                  f"金额=${result.get('amount_usd',0):.2f}  "
                  f"路由费=${result.get('routing_fee_usd',0):.2f}  "
                  f"签名={result.get('signature','')}")
        else:
            print(f"  #{i} [{merchant}] {status}  "
                  f"拼单={result.get('bundling',0)} 替代={result.get('alternatives',0)}")

        # 展示阶梯报价
        for tq in result.get("tiered_quotes", [])[:1]:
            for tier in tq.get("tiers", []):
                from modules.supply_chain.tiered_quote import TieredQuoteEngine
                print(f"      [阶梯] {TieredQuoteEngine.format_tier_display(tier)}")

        for line in result.get("log", [])[:2]:
            if "[TIER]" not in line:  # 阶梯报价已单独展示
                print(f"      {line}")
        print()

    print("=" * 70)
    print("  演示完成")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
