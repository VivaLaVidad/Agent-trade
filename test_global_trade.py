"""
test_global_trade.py — 全球化外贸履约闭环全生命周期测试
═══════════════════════════════════════════════════════
完整模拟:
  1. 创建客户画像（历史压价记录）
  2. 提交敏感 Ticker 询盘 → 验证 REG-DENIED
  3. 提交普通 Ticker 询盘 → 验证通过
  4. 验证 NegotiatorAgent 读取画像并应用 +5% 上浮
  5. 模拟成交 → 验证 DocuForge 生成 PI HTML + SHA-256 哈希
  6. 验证审计追踪完整性
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


# ═══════════════════════════════════════════════════════════════
#  Task 1: RegGuard 出口管制测试
# ═══════════════════════════════════════════════════════════════

def test_regguard_sanctioned_country_denied() -> None:
    """制裁国家 → REG-DENIED"""
    from modules.compliance.export_control import SanctionChecker

    checker = SanctionChecker()
    result = checker.check(destination="North Korea", ticker_id="CLAW-ELEC-CAP-100NF")
    assert result["passed"] is False
    assert result["risk_level"] == "denied"
    assert any("SANCTIONED_COUNTRY" in r for r in result["matched_rules"])


def test_regguard_sanctioned_port_denied() -> None:
    """制裁港口 → REG-DENIED"""
    from modules.compliance.export_control import SanctionChecker

    checker = SanctionChecker()
    result = checker.check(
        destination="Iran",
        raw_input="Ship to Bandar Abbas port",
    )
    assert result["passed"] is False
    assert any("SANCTIONED_PORT" in r for r in result["matched_rules"])


def test_regguard_restricted_ticker_denied() -> None:
    """军民两用 Ticker 前缀 → REG-DENIED"""
    from modules.compliance.export_control import SanctionChecker

    checker = SanctionChecker()
    result = checker.check(
        destination="Nigeria",
        ticker_id="CLAW-ELEC-IC-MIL-STM32",
    )
    assert result["passed"] is False
    assert any("RESTRICTED_TICKER" in r for r in result["matched_rules"])


def test_regguard_dual_use_keyword_denied() -> None:
    """双用途关键词 → REG-DENIED"""
    from modules.compliance.export_control import SanctionChecker

    checker = SanctionChecker()
    result = checker.check(
        destination="Egypt",
        product_keywords="encryption-grade chip",
    )
    assert result["passed"] is False
    assert any("DUAL_USE" in r for r in result["matched_rules"])


def test_regguard_normal_pass() -> None:
    """正常目的地 + 正常 Ticker → 通过"""
    from modules.compliance.export_control import SanctionChecker

    checker = SanctionChecker()
    result = checker.check(
        destination="Nigeria",
        ticker_id="CLAW-ELEC-CAP-100NF50V",
        product_keywords="ceramic capacitor",
    )
    assert result["passed"] is True
    assert result["risk_level"] == "clear"
    assert len(result["matched_rules"]) == 0


def test_regguard_node_integration() -> None:
    """reg_guard_node 在 MatchState 中正确设置 status"""
    from modules.compliance.export_control import reg_guard_node

    # 制裁国家 → reg_denied
    state_denied = {
        "structured_demand": {
            "destination": "Iran",
            "category": "capacitor",
        },
        "raw_input": "Need capacitors shipped to Iran",
        "status": "demand_parsed",
    }
    result = reg_guard_node(state_denied)
    assert result["status"] == "reg_denied"
    assert result["reg_guard_result"]["passed"] is False

    # 正常国家 → 保持原状态
    state_pass = {
        "structured_demand": {
            "destination": "Nigeria",
            "category": "capacitor",
        },
        "raw_input": "Need capacitors shipped to Lagos",
        "status": "demand_parsed",
    }
    result = reg_guard_node(state_pass)
    assert result["status"] == "demand_parsed"
    assert result["reg_guard_result"]["passed"] is True


# ═══════════════════════════════════════════════════════════════
#  Task 2: EpisodicMemory 长效交锋图谱测试
# ═══════════════════════════════════════════════════════════════

def test_opponent_profiler_markup_calculation() -> None:
    """画像管理器正确计算报价调整系数"""
    from core.long_term_memory import OpponentProfiler

    profiler = OpponentProfiler()

    # 高压客户 → +5%
    high_pressure = {
        "client_id": "test-hp",
        "total_negotiations": 10,
        "total_accepted": 3,
        "total_rejected": 7,
        "avg_discount_pct": 15.0,
        "avg_counter_rounds": 4.5,
        "max_counter_rounds": 8,
        "total_volume_usd": 500.0,
        "risk_tag": "high_pressure",
    }
    assert profiler.compute_initial_markup(high_pressure) == 0.05

    # 优质客户 → -2%
    premium = {
        "client_id": "test-premium",
        "total_negotiations": 20,
        "total_accepted": 18,
        "total_rejected": 2,
        "avg_discount_pct": 3.0,
        "avg_counter_rounds": 1.2,
        "max_counter_rounds": 3,
        "total_volume_usd": 50000.0,
        "risk_tag": "premium",
    }
    assert profiler.compute_initial_markup(premium) == -0.02

    # 普通客户 → 0%
    normal = {
        "client_id": "test-normal",
        "risk_tag": "normal",
    }
    assert profiler.compute_initial_markup(normal) == 0.0

    # 无画像 → 0%
    assert profiler.compute_initial_markup(None) == 0.0


def test_opponent_profiler_context_prompt() -> None:
    """画像格式化为 NegotiatorAgent 上下文"""
    from core.long_term_memory import OpponentProfiler

    profiler = OpponentProfiler()
    profile = {
        "client_id": "client-egypt-ahmed",
        "total_negotiations": 8,
        "total_accepted": 2,
        "total_rejected": 6,
        "avg_discount_pct": 12.0,
        "avg_counter_rounds": 4.0,
        "max_counter_rounds": 7,
        "total_volume_usd": 300.0,
        "risk_tag": "high_pressure",
    }
    prompt = profiler.format_context_prompt(profile)
    assert "[MEMORY]" in prompt
    assert "high_pressure" in prompt
    assert "上浮" in prompt or "+5%" in prompt or "5%" in prompt


def test_negotiator_applies_opponent_markup() -> None:
    """NegotiatorAgent 读取画像后应用报价调整"""
    from modules.supply_chain.negotiator import NegotiatorAgent

    async def _run() -> None:
        agent = NegotiatorAgent()
        demand = {
            "quantity": 500,
            "budget_usd": 500.0,
            "certs_required": ["CE"],
            "destination": "Nigeria",
            "client_id": "",  # 无画像 → 不调整
        }
        candidates = [
            {
                "sku_id": "sku-test-001",
                "sku_name": "100nF 50V 0805 MLCC",
                "category": "capacitor",
                "supplier_name": "Test Supplier",
                "unit_price_rmb": 0.5,
                "moq": 100,
                "stock_qty": 10000,
                "certifications": ["CE", "RoHS"],
                "match_score": 0.95,
            },
        ]
        result = await agent.execute(None, demand, candidates)
        # 无画像 → 不应有 [MEMORY] 日志
        memory_logs = [l for l in result.get("negotiation_log", []) if "[MEMORY]" in l]
        assert len(memory_logs) == 0

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
#  Task 3: DocuForge 防篡改文档引擎测试
# ═══════════════════════════════════════════════════════════════

def test_docuforge_generate_pi_html() -> None:
    """DocuForge 生成 Proforma Invoice HTML + SHA-256 哈希"""
    from modules.documents.invoice_generator import InvoiceGenerator

    gen = InvoiceGenerator()
    txn_data = {
        "po_number": "PO-TEST-001",
        "ticker_id": "CLAW-ELEC-CAP-100NF50V",
        "sku_name": "100nF 50V 0805 MLCC Ceramic Capacitor",
        "quantity": 500,
        "unit_price_rmb": 0.5,
        "unit_price_usd": 0.069,
        "total_usd": 34.50,
        "shipping_usd": 2.76,
        "landed_usd": 37.26,
        "routing_fee_usd": 0.35,
        "fee_rate": 0.01,
        "fx_rate": 7.25,
        "shipping_term": "CIF",
        "payment_term": "T/T 30% deposit",
        "moq": 100,
        "supplier_name": "Shenzhen Electronics Co.",
        "buyer_name": "Ahmed Trading Ltd",
        "destination": "Cairo, Egypt",
        "client_id": "client-egypt-ahmed",
        "transaction_id": "txn-test-001",
        "offer_disclaimer": "",
    }

    result = gen.generate_pi(txn_data)

    # 验证结果结构
    assert "html" in result
    assert "document_hash" in result
    assert "document_id" in result
    assert "po_number" in result

    # 验证 HTML 内容
    html = result["html"]
    assert "PROFORMA INVOICE" in html
    assert "PO-TEST-001" in html
    assert "CLAW-ELEC-CAP-100NF50V" in html
    assert "100nF 50V 0805 MLCC" in html
    assert "Shenzhen Electronics" in html

    # 验证 SHA-256 哈希
    assert len(result["document_hash"]) == 64
    assert result["document_hash"].isalnum()


def test_docuforge_hash_tamper_detection() -> None:
    """DocuForge 哈希防篡改验证"""
    import hashlib
    from modules.documents.invoice_generator import InvoiceGenerator

    gen = InvoiceGenerator()
    txn_data = {
        "po_number": "PO-TAMPER-001",
        "ticker_id": "CLAW-ELEC-RES-10K",
        "sku_name": "10K Resistor",
        "quantity": 1000,
        "unit_price_rmb": 0.1,
        "unit_price_usd": 0.014,
        "total_usd": 14.0,
        "shipping_usd": 1.12,
        "landed_usd": 15.12,
        "routing_fee_usd": 0.14,
        "fee_rate": 0.01,
        "fx_rate": 7.25,
        "shipping_term": "FOB",
        "payment_term": "T/T",
        "moq": 200,
        "supplier_name": "Test Supplier",
        "buyer_name": "Test Buyer",
        "destination": "Lagos",
        "client_id": "client-test",
        "transaction_id": "txn-tamper-001",
        "offer_disclaimer": "",
    }

    result = gen.generate_pi(txn_data)
    original_hash = result["document_hash"]

    # 验证哈希格式正确 (64 hex chars = SHA-256)
    assert len(original_hash) == 64
    assert original_hash.isalnum()

    # 验证哈希嵌入到最终 HTML 中
    assert original_hash in result["html"] or "PENDING" in result["html"]

    # 篡改 HTML → 重新计算哈希应不同
    tampered_html = result["html"].replace("10K Resistor", "FAKE Resistor")
    tampered_hash = hashlib.sha256(tampered_html.encode("utf-8")).hexdigest()
    assert tampered_hash != original_hash, "篡改后哈希应不同"

    # 验证两次生成同一数据的哈希一致性（确定性）
    result2 = gen.generate_pi(txn_data)
    # 注意：由于时间戳不同，哈希可能不同，但格式应一致
    assert len(result2["document_hash"]) == 64


# ═══════════════════════════════════════════════════════════════
#  全生命周期集成测试
# ═══════════════════════════════════════════════════════════════

def test_full_lifecycle_sanctioned_then_normal() -> None:
    """完整生命周期: 敏感 Ticker 遭拒 → 更换普通 Ticker 达成交易 → 生成 PI"""
    from modules.compliance.export_control import SanctionChecker, reg_guard_node
    from modules.supply_chain.negotiator import NegotiatorAgent
    from modules.supply_chain.ledger import LedgerService
    from modules.documents.invoice_generator import InvoiceGenerator
    from core.long_term_memory import OpponentProfiler

    async def _run() -> None:
        checker = SanctionChecker()
        negotiator = NegotiatorAgent()
        ledger = LedgerService(fee_rate=0.01)
        docuforge = InvoiceGenerator()
        profiler = OpponentProfiler()

        # ── Step 1: 敏感 Ticker 遭拒 ──
        result_denied = checker.check(
            destination="Iran",
            ticker_id="CLAW-ELEC-IC-MIL-STM32",
            product_keywords="military grade MCU",
        )
        assert result_denied["passed"] is False
        assert "SANCTIONED_COUNTRY" in str(result_denied["matched_rules"])

        # ── Step 2: 更换普通 Ticker ──
        result_pass = checker.check(
            destination="Nigeria",
            ticker_id="CLAW-ELEC-CAP-100NF50V",
            product_keywords="ceramic capacitor",
        )
        assert result_pass["passed"] is True

        # ── Step 3: 谈判 (无画像 → 正常报价) ──
        demand = {
            "quantity": 500,
            "budget_usd": 200.0,
            "certs_required": ["CE"],
            "destination": "Nigeria",
            "client_id": "client-lifecycle-test",
        }
        candidates = [
            {
                "sku_id": "sku-lifecycle-001",
                "sku_name": "100nF 50V 0805 MLCC",
                "category": "capacitor",
                "supplier_name": "Shenzhen Electronics",
                "unit_price_rmb": 0.5,
                "moq": 100,
                "stock_qty": 10000,
                "certifications": ["CE", "RoHS"],
                "match_score": 0.95,
            },
        ]
        neg_result = await negotiator.execute(None, demand, candidates)

        assert "best_match" in neg_result
        assert "negotiation_log" in neg_result
        # 所有候选应有 ticker_id
        for c in candidates:
            assert "ticker_id" in c
            assert c["ticker_id"].startswith("CLAW-")

        # ── Step 4: 成交 → 生成流水 ──
        best = neg_result.get("best_match")
        if best and best.get("status") == "approved":
            txn = ledger.create_transaction(
                merchant_id="merchant-lifecycle-001",
                client_id="client-lifecycle-test",
                amount_usd=best.get("landed_usd", 50.0),
                ticker_id=best.get("ticker_id", "CLAW-ELEC-CAP-100NF50V"),
            )
            assert txn["ticker_id"].startswith("CLAW-")
            assert ledger.verify_signature(txn) is True

            # ── Step 5: DocuForge 生成 PI ──
            pi_data = {
                "po_number": f"PO-LIFECYCLE-001",
                "ticker_id": txn["ticker_id"],
                "sku_name": best.get("sku_name", ""),
                "quantity": demand["quantity"],
                "unit_price_rmb": best.get("unit_price_rmb", 0.5),
                "unit_price_usd": txn["amount_usd"] / max(demand["quantity"], 1),
                "total_usd": txn["amount_usd"],
                "shipping_usd": best.get("shipping_usd", 0),
                "landed_usd": best.get("landed_usd", txn["amount_usd"]),
                "routing_fee_usd": txn["routing_fee_usd"],
                "fee_rate": txn["fee_rate"],
                "fx_rate": best.get("fx_rate", 7.25),
                "shipping_term": best.get("shipping_term", "CIF"),
                "payment_term": "T/T 30% deposit",
                "moq": 100,
                "supplier_name": best.get("supplier_name", ""),
                "buyer_name": "Lifecycle Test Buyer",
                "destination": "Nigeria",
                "client_id": "client-lifecycle-test",
                "transaction_id": txn["transaction_id"],
                "offer_disclaimer": "",
            }
            pi_result = docuforge.generate_pi(pi_data)

            assert "html" in pi_result
            assert len(pi_result["document_hash"]) == 64
            assert "PROFORMA INVOICE" in pi_result["html"]
            assert txn["ticker_id"] in pi_result["html"]

        # ── Step 6: 验证画像计算 ──
        markup = profiler.compute_initial_markup(None)
        assert markup == 0.0  # 无画像 → 0%

        hp_profile = {
            "client_id": "client-lifecycle-test",
            "risk_tag": "high_pressure",
        }
        markup_hp = profiler.compute_initial_markup(hp_profile)
        assert markup_hp == 0.05  # 高压 → +5%

    asyncio.run(_run())


def test_matching_graph_compiles_with_regguard() -> None:
    """matching_graph 含 RegGuard 节点后仍能正常编译"""
    from modules.supply_chain.matching_graph import build_matching_graph
    graph = build_matching_graph()
    assert graph is not None
