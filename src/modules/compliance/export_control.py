"""
modules.compliance.export_control — RegGuard 双轨合规节点
═══════════════════════════════════════════════════════════════
职责：
  1. 出口管制检查（原有）：制裁国家/港口/Ticker/双用途关键词
  2. 进口准入检查（新增）：目的地国家的行业认证要求
     - 通信准入（MCMC/SIRIM、MIC、NBTC、IMDA、WPC/TEC）
     - 矿安防爆准入（DOSH/JMG、DGMS — IECEx/ATEX/MA 映射）
  3. 认证等效映射：MA↔IECEx/ATEX、CE↔SIRIM、FCC↔MCMC 等
  4. 命中 → ComplianceException / 降权预警，状态机路由到 END 或标记风险
  5. MarketDataBus 广播 REG_DENIED 事件，审计面板显示红色 [REG-DENIED]

暗箱原则：
  - 黑名单 + 认证数据本地化，零公网依赖
  - 所有拦截记录经 ComplianceGateway 加密审计
"""

from __future__ import annotations

import json
import os
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

_KEYWORDS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "embargo_keywords.json",
)


class ComplianceException(Exception):
    """出口管制合规异常 — 命中制裁/管制名单时触发

    Attributes
    ----------
    reason : str
        拦截原因
    matched_rules : list[str]
        命中的具体规则
    """

    def __init__(self, reason: str, matched_rules: list[str] | None = None) -> None:
        self.reason = reason
        self.matched_rules = matched_rules or []
        super().__init__(reason)


class EmbargoDatabase:
    """本地制裁/管制/认证数据库

    从 embargo_keywords.json 加载，支持热重载。
    包含出口管制黑名单 + 进口认证要求 + 认证等效映射。
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or _KEYWORDS_PATH
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            logger.info(
                "EmbargoDatabase 已加载: countries=%d ports=%d tickers=%d keywords=%d cert_countries=%d",
                len(self.sanctioned_countries),
                len(self.sanctioned_ports),
                len(self.restricted_ticker_prefixes),
                len(self.dual_use_keywords),
                len(self.import_certification_rules),
            )
        except FileNotFoundError:
            logger.warning("embargo_keywords.json 未找到，使用空黑名单")
            self._data = {}
        except Exception as exc:
            logger.error("EmbargoDatabase 加载失败: %s", exc)
            self._data = {}

    @property
    def sanctioned_countries(self) -> list[str]:
        return self._data.get("sanctioned_countries", [])

    @property
    def sanctioned_ports(self) -> list[str]:
        return self._data.get("sanctioned_ports", [])

    @property
    def restricted_ticker_prefixes(self) -> list[str]:
        return self._data.get("restricted_ticker_prefixes", [])

    @property
    def dual_use_keywords(self) -> list[str]:
        return self._data.get("dual_use_keywords", [])

    @property
    def import_certification_rules(self) -> dict[str, Any]:
        return self._data.get("import_certification_rules", {})

    @property
    def cert_equivalence_map(self) -> dict[str, Any]:
        return self._data.get("cert_equivalence_map", {})


class ImportCertChecker:
    """进口准入认证检查器

    根据目的地国家 + 产品关键词，自动匹配所需的行业认证要求。
    支持认证等效映射（如 MA → IECEx/ATEX）。

    检查维度：
      1. 通信准入（SIRIM/MCMC、MIC、NBTC、IMDA、WPC/TEC）
      2. 矿安防爆准入（IECEx/ATEX、DOSH/JMG、DGMS）
    """

    def __init__(self, db: EmbargoDatabase | None = None) -> None:
        self._db = db or EmbargoDatabase()

    def check(
        self,
        destination: str = "",
        product_keywords: str = "",
        raw_input: str = "",
        supplier_certs: list[str] | None = None,
    ) -> dict[str, Any]:
        """执行进口准入认证检查

        Parameters
        ----------
        destination : str
            目的地国家
        product_keywords : str
            产品关键词
        raw_input : str
            原始询盘文本
        supplier_certs : list[str] | None
            供应商已持有的认证列表

        Returns
        -------
        dict
            {
                "passed": bool,
                "warnings": list[dict],  -- 需要但缺失的认证
                "matched_sectors": list[str],  -- 命中的行业领域
                "required_certs": list[dict],  -- 所有适用的认证要求
                "details": str,
            }
        """
        supplier_certs = supplier_certs or []
        combined_text = f"{product_keywords} {raw_input}".lower()
        dest_normalized = self._normalize_country(destination)

        country_rules = self._db.import_certification_rules.get(dest_normalized, {})
        if not country_rules:
            return {
                "passed": True,
                "warnings": [],
                "matched_sectors": [],
                "required_certs": [],
                "details": f"目的地 {destination} 无特定进口认证要求（或尚未录入）",
            }

        warnings: list[dict[str, Any]] = []
        matched_sectors: list[str] = []
        required_certs: list[dict[str, Any]] = []

        for sector_name, sector_rules in country_rules.items():
            keywords = sector_rules.get("keywords", [])
            if not self._text_matches_keywords(combined_text, keywords):
                continue

            matched_sectors.append(sector_name)
            req_certs = sector_rules.get("required_certs", [])
            equiv_certs = sector_rules.get("equivalent_certs", [])
            authority = sector_rules.get("authority", "Unknown")
            description = sector_rules.get("description", "")

            cert_entry = {
                "sector": sector_name,
                "authority": authority,
                "required_certs": req_certs,
                "equivalent_certs": equiv_certs,
                "description": description,
            }
            required_certs.append(cert_entry)

            # Check if supplier has any of the required or equivalent certs
            all_acceptable = set(c.upper() for c in req_certs + equiv_certs)
            # Also check equivalence map for transitive matches
            expanded = set(all_acceptable)
            for cert in list(all_acceptable):
                equiv_entry = self._db.cert_equivalence_map.get(cert, {})
                for intl in equiv_entry.get("intl_equivalent", []):
                    expanded.add(intl.upper())

            supplier_upper = set(c.upper() for c in supplier_certs)
            has_valid_cert = bool(supplier_upper & expanded)

            if not has_valid_cert:
                warnings.append({
                    "sector": sector_name,
                    "authority": authority,
                    "missing_certs": req_certs,
                    "acceptable_alternatives": equiv_certs,
                    "severity": "critical",
                    "message": (
                        f"[{sector_name.upper()}] 目的地 {dest_normalized} 要求 "
                        f"{'/'.join(req_certs)} 认证（{authority}）。"
                        f"可接受等效认证: {', '.join(equiv_certs)}。"
                        f"供应商当前认证: {', '.join(supplier_certs) if supplier_certs else '无'}。"
                    ),
                })

        passed = len(warnings) == 0

        if warnings:
            logger.warning(
                "ImportCertChecker 预警: destination=%s sectors=%s warnings=%d",
                dest_normalized, matched_sectors, len(warnings),
            )

        return {
            "passed": passed,
            "warnings": warnings,
            "matched_sectors": matched_sectors,
            "required_certs": required_certs,
            "details": (
                f"进口认证检查通过 ({dest_normalized})"
                if passed
                else f"进口认证缺失: {len(warnings)} 项认证预警 — "
                     + "; ".join(w["message"][:80] for w in warnings[:3])
            ),
        }

    @staticmethod
    def _normalize_country(destination: str) -> str:
        """Normalize destination to country name for rule lookup."""
        dest = destination.strip()
        # Common aliases
        aliases: dict[str, str] = {
            "my": "Malaysia", "malaysia": "Malaysia", "kuala lumpur": "Malaysia",
            "kl": "Malaysia", "penang": "Malaysia", "johor": "Malaysia",
            "sabah": "Malaysia", "sarawak": "Malaysia", "port klang": "Malaysia",
            "vn": "Vietnam", "vietnam": "Vietnam", "ho chi minh": "Vietnam",
            "hanoi": "Vietnam", "hai phong": "Vietnam",
            "th": "Thailand", "thailand": "Thailand", "bangkok": "Thailand",
            "sg": "Singapore", "singapore": "Singapore",
            "in": "India", "india": "India", "mumbai": "India",
            "delhi": "India", "chennai": "India", "kolkata": "India",
        }
        return aliases.get(dest.lower(), dest)

    @staticmethod
    def _text_matches_keywords(text: str, keywords: list[str]) -> bool:
        """Check if text contains any of the sector keywords."""
        return any(kw.lower() in text for kw in keywords)


class SanctionChecker:
    """出口管制合规检查器

    检查维度：
      1. 目的地国家 → sanctioned_countries
      2. 目的地港口 → sanctioned_ports
      3. Ticker 前缀 → restricted_ticker_prefixes (军民两用)
      4. 产品关键词 → dual_use_keywords
    """

    def __init__(self, db: EmbargoDatabase | None = None) -> None:
        self._db = db or EmbargoDatabase()

    def check(
        self,
        destination: str = "",
        ticker_id: str = "",
        product_keywords: str = "",
        category: str = "",
        raw_input: str = "",
    ) -> dict[str, Any]:
        """执行合规检查

        Parameters
        ----------
        destination : str
            目的地国家/城市
        ticker_id : str
            标准化 Ticker ID
        product_keywords : str
            产品关键词
        category : str
            品类
        raw_input : str
            原始询盘文本

        Returns
        -------
        dict
            {
                "passed": bool,
                "matched_rules": list[str],
                "risk_level": "clear" | "denied",
                "details": str,
            }
        """
        matched: list[str] = []
        combined_text = f"{destination} {product_keywords} {category} {raw_input}".lower()

        # 1. 国家制裁检查
        dest_lower = destination.lower().strip()
        for country in self._db.sanctioned_countries:
            if country.lower() in dest_lower or dest_lower in country.lower():
                matched.append(f"SANCTIONED_COUNTRY:{country}")

        # 2. 港口制裁检查
        for port in self._db.sanctioned_ports:
            if port.lower() in combined_text:
                matched.append(f"SANCTIONED_PORT:{port}")

        # 3. Ticker 前缀管制检查
        ticker_upper = ticker_id.upper()
        for prefix in self._db.restricted_ticker_prefixes:
            if ticker_upper.startswith(prefix):
                matched.append(f"RESTRICTED_TICKER:{prefix}")

        # 4. 双用途关键词检查
        for keyword in self._db.dual_use_keywords:
            if keyword.lower() in combined_text:
                matched.append(f"DUAL_USE:{keyword}")

        passed = len(matched) == 0
        risk_level = "clear" if passed else "denied"

        if not passed:
            logger.warning(
                "RegGuard 拦截: destination=%s ticker=%s rules=%s",
                destination, ticker_id, matched,
            )

        return {
            "passed": passed,
            "matched_rules": matched,
            "risk_level": risk_level,
            "details": (
                f"出口管制检查通过" if passed
                else f"出口管制熔断: 命中 {len(matched)} 条规则 — {', '.join(matched[:5])}"
            ),
        }


def reg_guard_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph 节点: RegGuard 双轨合规检查

    Track 1: 出口管制（制裁国家/港口/Ticker/双用途关键词）→ 命中即熔断
    Track 2: 进口准入认证（SIRIM/IECEx/ATEX 等）→ 缺失则预警 + 降权

    Parameters
    ----------
    state : MatchState
        撮合工作流状态

    Returns
    -------
    dict
        更新的状态字段
    """
    demand = state.get("structured_demand", {})
    raw_input = state.get("raw_input", "")

    destination = demand.get("destination", demand.get("buyer_country", ""))
    category = demand.get("category", "")
    product_kw = demand.get("product_keywords", demand.get("product", ""))

    # 尝试从 Ticker 注册表获取 Ticker ID
    ticker_id = ""
    if category:
        try:
            from core.ticker_plant import get_ticker_registry
            registry = get_ticker_registry()
            ticker = registry.resolve(category, product_kw or category)
            ticker_id = ticker.ticker_id
        except Exception:
            pass

    db = EmbargoDatabase()

    # ── Track 1: 出口管制检查 ──
    sanction_checker = SanctionChecker(db=db)
    export_result = sanction_checker.check(
        destination=destination,
        ticker_id=ticker_id,
        product_keywords=product_kw,
        category=category,
        raw_input=raw_input,
    )

    if not export_result["passed"]:
        # 出口管制命中 → 硬熔断
        _broadcast_reg_denied(ticker_id, destination, export_result["matched_rules"])
        _audit_reg_denied(destination, ticker_id, export_result)

        return {
            "reg_guard_result": export_result,
            "import_cert_result": None,
            "status": "reg_denied",
            "error": export_result["details"],
        }

    # ── Track 2: 进口准入认证检查 ──
    supplier_certs = demand.get("supplier_certs", [])
    # Also check candidates for certs if available
    candidates = state.get("candidates", [])
    if candidates and not supplier_certs:
        for c in candidates:
            certs = c.get("certs", c.get("certifications", []))
            if certs:
                supplier_certs = certs
                break

    import_checker = ImportCertChecker(db=db)
    import_result = import_checker.check(
        destination=destination,
        product_keywords=product_kw,
        raw_input=raw_input,
        supplier_certs=supplier_certs,
    )

    if not import_result["passed"]:
        # 进口认证缺失 → 预警（不熔断，但标记风险 + 降权）
        logger.warning(
            "RegGuard 进口认证预警: destination=%s sectors=%s",
            destination, import_result["matched_sectors"],
        )
        _broadcast_cert_warning(ticker_id, destination, import_result["warnings"])

    logger.info(
        "RegGuard 通过: destination=%s ticker=%s import_warnings=%d",
        destination, ticker_id, len(import_result.get("warnings", [])),
    )

    return {
        "reg_guard_result": export_result,
        "import_cert_result": import_result,
        "status": state.get("status", "demand_parsed"),
    }


def _broadcast_reg_denied(
    ticker_id: str,
    destination: str,
    matched_rules: list[str],
) -> None:
    """广播 REG_DENIED 事件"""
    try:
        from core.ticker_plant import EventType, MarketEvent, get_market_bus
        import asyncio

        event = MarketEvent(
            event_type=EventType.NEGOTIATION_UPDATE,
            ticker_id=ticker_id or "SYSTEM",
            data={
                "action": "REG_DENIED",
                "destination": destination,
                "matched_rules": matched_rules[:5],
                "severity": "critical",
            },
        )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(get_market_bus().publish(event))
        except RuntimeError:
            pass
    except Exception as exc:
        logger.debug("REG_DENIED 广播跳过: %s", exc)


def _broadcast_cert_warning(
    ticker_id: str,
    destination: str,
    warnings: list[dict[str, Any]],
) -> None:
    """广播 CERT_WARNING 事件（进口认证缺失预警）"""
    try:
        from core.ticker_plant import EventType, MarketEvent, get_market_bus
        import asyncio

        event = MarketEvent(
            event_type=EventType.NEGOTIATION_UPDATE,
            ticker_id=ticker_id or "SYSTEM",
            data={
                "action": "CERT_WARNING",
                "destination": destination,
                "missing_sectors": [w["sector"] for w in warnings[:5]],
                "severity": "warning",
            },
        )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(get_market_bus().publish(event))
        except RuntimeError:
            pass
    except Exception as exc:
        logger.debug("CERT_WARNING 广播跳过: %s", exc)


def _audit_reg_denied(
    destination: str,
    ticker_id: str,
    result: dict[str, Any],
) -> None:
    """加密审计 REG_DENIED 事件"""
    try:
        from modules.audit_module.compliance_gateway import get_compliance_gateway

        gateway = get_compliance_gateway()
        gateway.encrypt_and_log(
            module="reg_guard",
            action="export_control_denied",
            raw_data={
                "destination": destination,
                "ticker_id": ticker_id,
                "matched_rules": result.get("matched_rules", []),
                "risk_level": result.get("risk_level", "denied"),
            },
            operator="system:reg_guard",
        )
    except Exception as exc:
        logger.debug("REG_DENIED 审计跳过: %s", exc)
