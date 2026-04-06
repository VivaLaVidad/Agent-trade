"""
test_brain.py — 外贸邮件自动化工作流端到端测试
──────────────────────────────────────────────
模拟传入一封冗长且带有大量拼写错误的海外客户英文询盘邮件，
完整打印 LangGraph 运行后的意图解析结果与最终回信草稿。
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from agents.workflow_graph import build_trade_graph


# ─── 测试邮件：冗长 + 大量拼写错误的真实询盘 ────────────────
TEST_INQUIRY: str = """\
Subject: RE: URGNET - Need quoation for solar panles and invertors ASAP!!!

Dear Sales Representtive,

My name is Rajesh Patel and I am the procurement maneger at Green Energy \
Solutions Pvt Ltd, based in Mumbai, India. I got your compnay contact from \
the Canton Fair catalouge last year and I have been meanng to reach out for \
quite some tiem now.

We are currenlty working on a very larg goverment project to install solar \
power systms across 15 rural schools in Maharashtra state. This is a \
goverment-funded intiative under the National Solar Mission scheme, and we \
have a very strict deatline to meet.

Here is what we need:

1. Monocrystalline Solar Pannels (400W each) - Quanttiy: 500 pieces
   They MUST have IEC 61215 and TUV certfication. This is an absolut \
requirement for the goverment tender.

2. Hybrid Solar Invertors (5kW) - Quanttiy: 30 units
   Preferbly with MPPT technolgy and WiFi monitring capabilty.

3. Aluminum Mounting Structres - for 500 pannels on tiled roofs

Our totall buget for the entire project is approxmatley USD 180,000 to \
USD 200,000 including CIF shipping to Mumbai port (JNPT). We are also open \
to FOB pricing if that works out cheeper.

The project deatline is extremly tight - we need all goods delivred to \
Mumbai within 45 days from order confrimation. If you canot meet this \
tiemline, please let us know immedately so we can considr alternetive \
suppliers.

We have been in the renewble energy busness for over 12 years and have \
completd more than 200 installtion projects across India. We are lookng \
for a reliabel long-term suplier who can support us with compettive \
pricing and consitent quaity.

Please send us your best FOB and CIF quoation at your earliset conveniece. \
Also, can you arraneg to send us product samles and techinical datasheets?

I would apprecate if you could repond within 2-3 busness days as we need \
to submit our final bid to the goverment by end of this month.

Thank you for your tiem and I look forwrd to a fruitfl business \
relationsship.

Best Regards,
Rajesh Patel
Senior Procurement Manager
Green Energy Solutions Pvt Ltd
Mumbai, India
Tel: +91 22 2345 6789
Mobile: +91 98765 43210
Email: rajesh@greenenergy.co.in\
"""


async def main() -> None:
    graph = build_trade_graph()

    print("=" * 70)
    print("  TradeStealth — 外贸邮件自动化工作流测试")
    print("=" * 70)
    print(f"\n[输入邮件] 长度 = {len(TEST_INQUIRY)} 字符")
    print("-" * 70)

    result: dict = await graph.ainvoke(
        {"raw_inquiry": TEST_INQUIRY},
        config={"configurable": {"thread_id": "test-rajesh-001"}},
    )

    # ── 意图解析结果 ──
    intent: dict = result.get("analyzed_intent", {})
    is_valid: bool = result.get("is_valid_lead", False)

    print("\n[1] 意图解析结果 (analyzed_intent):")
    print("-" * 70)
    print(json.dumps(intent, indent=2, ensure_ascii=False))

    print(f"\n[2] 是否有效线索: {is_valid}")
    print("-" * 70)

    # ── 回信草稿 ──
    response: str = result.get("generated_response", "")
    if response:
        print("\n[3] AI 生成的英文回信草稿 (generated_response):")
        print("-" * 70)
        print(response)
    else:
        print("\n[3] 未生成回信（该询盘被判定为无效线索 / 垃圾邮件）")

    print("\n" + "=" * 70)
    print("  测试完成")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
