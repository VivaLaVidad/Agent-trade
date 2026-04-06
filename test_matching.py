"""
test_matching.py — 全球工业品撮合引擎端到端测试
──────────────────────────────────────────────
1. 初始化 SQLite 数据库 + 生成 50 供应商 / 200 SKU 模拟数据
2. 模拟海外买家发来一段带拼写错误的电子元器件采购需求
3. 完整运行 LangGraph 撮合工作流：需求解析 → 供应商检索 → 谈判 → 订单生成
4. 打印每一步的结构化输出
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from dotenv import load_dotenv
load_dotenv()

TEST_BUYER_REQUEST: str = """\
Hi there,

I am Okechukwu from Lagos, Nigeria. I work for BrightTech Solutions Ltd.

We urgantly need to purchase electronik components for a new solar inverter \
project we are bilding. Here is what we need:

1. Ceramic capacitors - 100uF, 25V rating, SMD packge - Quantty: 2000 pcs
   Must have CE and RoHS certification.

2. Also need some LDO voltage regulators if you have them.

Our total budgt is around USD 800 for the capacitors. We need delivery \
within 3 weeks if possible.

Please provide your best CIF Lagos price.

Best regards,
Okechukwu Nnamdi
BrightTech Solutions Ltd
Lagos, Nigeria
Tel: +234-802-345-6789\
"""


async def main() -> None:
    import modules.supply_chain.models  # noqa: F401  先注册供应链表到 Base.metadata
    from database.models import Base, async_engine, AsyncSessionFactory

    print("=" * 70)
    print("  Project Claw — 全球工业品撮合引擎测试")
    print("=" * 70)

    # ── Step 1: 初始化数据库 ──
    print("\n[1/5] 初始化数据库 + 生成模拟供应商目录...")
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    from modules.supply_chain.mock_data import generate_mock_catalog
    stats = await generate_mock_catalog(num_suppliers=50, num_skus=200)
    print(f"       已生成 {stats['suppliers']} 家供应商, {stats['skus']} 个 SKU")

    # ── Step 2: 显示买家需求 ──
    print(f"\n[2/5] 海外买家询盘 ({len(TEST_BUYER_REQUEST)} 字符):")
    print("-" * 70)
    print(TEST_BUYER_REQUEST[:500])
    print("-" * 70)

    # ── Step 3: 运行撮合工作流 ──
    print("\n[3/5] 启动 LangGraph 撮合工作流...")
    print("       demand_node → supply_node → negotiate_node → po_gen_node")
    print("       (需要调用 Ollama qwen3:4b，请耐心等待...)\n")

    from modules.supply_chain.matching_graph import MatchingOrchestrator
    orchestrator = MatchingOrchestrator()
    result = await orchestrator.run(TEST_BUYER_REQUEST, thread_id="test-okechukwu-001")

    # ── Step 4: 打印结果 ──
    demand = result.get("structured_demand", {})
    print("[4/5] 需求解析结果 (structured_demand):")
    print("-" * 70)
    print(json.dumps(demand, indent=2, ensure_ascii=False, default=str))

    candidates = result.get("candidates", [])
    print(f"\n       供应链匹配: 找到 {len(candidates)} 个候选 SKU")
    for i, c in enumerate(candidates[:3], 1):
        print(f"       #{i} {c.get('sku_name','')} @ {c.get('supplier_name','')} "
              f"— ¥{c.get('unit_price_rmb',0)}/个 "
              f"MOQ={c.get('moq',0)} "
              f"评分={c.get('match_score',0)}")

    neg = result.get("negotiation_result", {})
    if neg:
        print("\n       谈判日志:")
        for line in neg.get("negotiation_log", []):
            print(f"       {line}")

        best = neg.get("best_match")
        if best:
            print(f"\n       最佳匹配: {best.get('sku_name','')} "
                  f"@ {best.get('supplier_name','')} "
                  f"— 落地价 ${best.get('landed_usd',0)} {best.get('shipping_term','')}")

    # ── Step 5: 采购订单 ──
    po = result.get("purchase_order", {})
    if po and po.get("content"):
        print(f"\n[5/5] AI 生成的采购订单 (PO #{po.get('po_number','')}):")
        print("-" * 70)
        print(po["content"][:1500])
    else:
        print(f"\n[5/5] 未生成采购订单 — 状态: {result.get('status', 'unknown')}")
        if neg:
            alts = neg.get("alternatives", [])
            if alts:
                print(f"       提供了 {len(alts)} 个替代方案")
            bundles = neg.get("bundling_suggestions", [])
            if bundles:
                print(f"       提供了 {len(bundles)} 个拼单建议")

    print("\n" + "=" * 70)
    print(f"  测试完成 — 最终状态: {result.get('status', 'unknown')}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
