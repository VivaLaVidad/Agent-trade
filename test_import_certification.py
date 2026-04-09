"""
test_import_certification.py — 进口准入认证双轨合规测试
═══════════════════════════════════════════════════════
覆盖：
  - ImportCertChecker 基础功能
  - 马来西亚 SIRIM/MCMC 通信准入
  - 马来西亚 DOSH/JMG 矿安防爆准入
  - 认证等效映射（MA→IECEx, CE→SIRIM）
  - reg_guard_node 双轨集成
  - 国家别名归一化
"""

import pytest


# ── ImportCertChecker 单元测试 ──


def test_import_cert_malaysia_telecom_no_cert() -> None:
    """马来西亚 5G 设备无 SIRIM 认证 → 预警"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Malaysia",
        product_keywords="5G base station CPE",
        supplier_certs=[],
    )
    assert result["passed"] is False
    assert "telecom" in result["matched_sectors"]
    assert len(result["warnings"]) >= 1
    assert any("SIRIM" in w["message"] for w in result["warnings"])


def test_import_cert_malaysia_telecom_with_sirim() -> None:
    """马来西亚 5G 设备持有 SIRIM → 通过"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Malaysia",
        product_keywords="5G base station",
        supplier_certs=["SIRIM"],
    )
    assert result["passed"] is True
    assert "telecom" in result["matched_sectors"]
    assert len(result["warnings"]) == 0


def test_import_cert_malaysia_telecom_with_ce_equivalent() -> None:
    """马来西亚 5G 设备持有 CE（等效认证）→ 通过"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Malaysia",
        product_keywords="5G router wireless",
        supplier_certs=["CE"],
    )
    assert result["passed"] is True
    assert "telecom" in result["matched_sectors"]


def test_import_cert_malaysia_telecom_with_fcc_equivalent() -> None:
    """马来西亚 5G 设备持有 FCC（等效认证）→ 通过"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Malaysia",
        product_keywords="5G antenna RF",
        supplier_certs=["FCC"],
    )
    assert result["passed"] is True


def test_import_cert_malaysia_mining_no_cert() -> None:
    """马来西亚矿用设备无 IECEx/ATEX → 预警"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Malaysia",
        product_keywords="mining explosion-proof underground coal",
        supplier_certs=[],
    )
    assert result["passed"] is False
    assert "mining_safety" in result["matched_sectors"]
    assert any("IECEx" in w["message"] or "ATEX" in w["message"] for w in result["warnings"])


def test_import_cert_malaysia_mining_with_iecex() -> None:
    """马来西亚矿用设备持有 IECEx → 通过"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Malaysia",
        product_keywords="mining intrinsic safety underground",
        supplier_certs=["IECEx"],
    )
    assert result["passed"] is True
    assert "mining_safety" in result["matched_sectors"]


def test_import_cert_malaysia_mining_with_ma_equivalent() -> None:
    """马来西亚矿用设备持有 MA（中国煤安）→ 通过（等效映射 MA→IECEx）"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Malaysia",
        product_keywords="coal mine flameproof equipment",
        supplier_certs=["MA"],
    )
    assert result["passed"] is True
    assert "mining_safety" in result["matched_sectors"]


def test_import_cert_malaysia_dual_sector_5g_mining() -> None:
    """马来西亚矿用 5G 基站 → 同时命中 telecom + mining_safety 两个领域"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Malaysia",
        product_keywords="5G explosion-proof base station for underground mining",
        supplier_certs=[],
    )
    assert result["passed"] is False
    assert "telecom" in result["matched_sectors"]
    assert "mining_safety" in result["matched_sectors"]
    assert len(result["warnings"]) >= 2


def test_import_cert_malaysia_dual_sector_all_certs() -> None:
    """马来西亚矿用 5G 基站持有 SIRIM + IECEx → 双领域通过"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Malaysia",
        product_keywords="5G explosion-proof base station for underground mining",
        supplier_certs=["SIRIM", "IECEx"],
    )
    assert result["passed"] is True
    assert "telecom" in result["matched_sectors"]
    assert "mining_safety" in result["matched_sectors"]
    assert len(result["warnings"]) == 0


def test_import_cert_no_rules_country() -> None:
    """无认证规则的国家 → 直接通过"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Brazil",
        product_keywords="5G base station",
        supplier_certs=[],
    )
    assert result["passed"] is True
    assert len(result["matched_sectors"]) == 0


def test_import_cert_non_matching_product() -> None:
    """马来西亚但产品不匹配任何行业关键词 → 通过"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Malaysia",
        product_keywords="capacitor resistor 100nF",
        supplier_certs=[],
    )
    assert result["passed"] is True
    assert len(result["matched_sectors"]) == 0


def test_import_cert_vietnam_telecom() -> None:
    """越南 5G 设备无 MIC 认证 → 预警"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Vietnam",
        product_keywords="5G CPE router",
        supplier_certs=[],
    )
    assert result["passed"] is False
    assert "telecom" in result["matched_sectors"]


def test_import_cert_india_mining() -> None:
    """印度矿用设备无 DGMS → 预警"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="India",
        product_keywords="coal mine underground explosion-proof",
        supplier_certs=[],
    )
    assert result["passed"] is False
    assert "mining_safety" in result["matched_sectors"]


def test_import_cert_india_mining_with_iecex() -> None:
    """印度矿用设备持有 IECEx → 通过（等效 DGMS）"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="India",
        product_keywords="coal mine underground explosion-proof",
        supplier_certs=["IECEx"],
    )
    assert result["passed"] is True


# ── 国家别名归一化测试 ──


def test_country_alias_kuala_lumpur() -> None:
    """Kuala Lumpur → Malaysia"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Kuala Lumpur",
        product_keywords="5G base station",
        supplier_certs=[],
    )
    assert result["passed"] is False
    assert "telecom" in result["matched_sectors"]


def test_country_alias_ho_chi_minh() -> None:
    """Ho Chi Minh → Vietnam"""
    from modules.compliance.export_control import ImportCertChecker

    checker = ImportCertChecker()
    result = checker.check(
        destination="Ho Chi Minh",
        product_keywords="5G router",
        supplier_certs=[],
    )
    assert result["passed"] is False
    assert "telecom" in result["matched_sectors"]


# ── reg_guard_node 双轨集成测试 ──


def test_regguard_node_export_denied_still_blocks() -> None:
    """出口管制命中 → 硬熔断（不进入进口认证检查）"""
    from modules.compliance.export_control import reg_guard_node

    state = {
        "structured_demand": {
            "destination": "North Korea",
            "product_keywords": "5G base station",
            "category": "telecom",
        },
        "raw_input": "need 5G equipment for North Korea",
    }
    result = reg_guard_node(state)
    assert result["status"] == "reg_denied"
    assert result["reg_guard_result"]["passed"] is False
    assert result["import_cert_result"] is None


def test_regguard_node_export_pass_import_warning() -> None:
    """出口管制通过 + 进口认证缺失 → 状态正常但附带 import_cert_result 预警"""
    from modules.compliance.export_control import reg_guard_node

    state = {
        "structured_demand": {
            "destination": "Malaysia",
            "product_keywords": "5G explosion-proof base station mining",
            "category": "telecom",
        },
        "raw_input": "need 5G mining equipment for Malaysia coal mine",
        "status": "demand_parsed",
    }
    result = reg_guard_node(state)
    assert result["status"] == "demand_parsed"  # 不熔断
    assert result["reg_guard_result"]["passed"] is True
    assert result["import_cert_result"] is not None
    assert result["import_cert_result"]["passed"] is False
    assert len(result["import_cert_result"]["warnings"]) >= 1


def test_regguard_node_both_tracks_pass() -> None:
    """出口管制通过 + 进口认证通过 → 全绿"""
    from modules.compliance.export_control import reg_guard_node

    state = {
        "structured_demand": {
            "destination": "Malaysia",
            "product_keywords": "5G base station",
            "category": "telecom",
            "supplier_certs": ["SIRIM", "IECEx"],
        },
        "raw_input": "need 5G equipment for Malaysia",
        "status": "demand_parsed",
    }
    result = reg_guard_node(state)
    assert result["status"] == "demand_parsed"
    assert result["reg_guard_result"]["passed"] is True
    assert result["import_cert_result"]["passed"] is True


def test_regguard_node_generic_product_no_cert_needed() -> None:
    """通用电子元件到马来西亚 → 无行业认证要求"""
    from modules.compliance.export_control import reg_guard_node

    state = {
        "structured_demand": {
            "destination": "Malaysia",
            "product_keywords": "capacitor 100nF 0805",
            "category": "capacitor",
        },
        "raw_input": "need 10000pcs 100nF capacitors",
        "status": "demand_parsed",
    }
    result = reg_guard_node(state)
    assert result["status"] == "demand_parsed"
    assert result["reg_guard_result"]["passed"] is True
    assert result["import_cert_result"]["passed"] is True


# ── EmbargoDatabase 扩展字段测试 ──


def test_embargo_db_has_import_cert_rules() -> None:
    """EmbargoDatabase 正确加载 import_certification_rules"""
    from modules.compliance.export_control import EmbargoDatabase

    db = EmbargoDatabase()
    rules = db.import_certification_rules
    assert "Malaysia" in rules
    assert "telecom" in rules["Malaysia"]
    assert "mining_safety" in rules["Malaysia"]


def test_embargo_db_has_cert_equivalence_map() -> None:
    """EmbargoDatabase 正确加载 cert_equivalence_map"""
    from modules.compliance.export_control import EmbargoDatabase

    db = EmbargoDatabase()
    eq_map = db.cert_equivalence_map
    assert "MA" in eq_map
    assert "IECEx" in eq_map["MA"]["intl_equivalent"]
    assert "CE" in eq_map
    assert "SIRIM" in eq_map["CE"]["intl_equivalent"]


def test_embargo_db_backward_compatible() -> None:
    """EmbargoDatabase 原有字段不受影响"""
    from modules.compliance.export_control import EmbargoDatabase

    db = EmbargoDatabase()
    assert len(db.sanctioned_countries) > 0
    assert "North Korea" in db.sanctioned_countries
    assert len(db.dual_use_keywords) > 0
    assert len(db.restricted_ticker_prefixes) > 0
