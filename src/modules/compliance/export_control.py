"""
modules.compliance.export_control — RegGuard 出口管制合规节点
═══════════════════════════════════════════════════════════════
职责：
  1. 在 LangGraph 撮合工作流中，意图解析之后、供应链检索之前插入合规检查
  2. 加载本地 embargo_keywords.json（模拟黑名单）
  3. 检查目的地国家/港口、Ticker 前缀、双用途关键词
  4. 命中 → 触发 ComplianceException，状态机走到 END
  5. 在 MarketDataBus 广播 REG_DENIED 事件，bloomberg_tui 审计面板显示红色 [REG-DENIED]

暗箱原则：
  - 黑名单数据本地化，零公网依赖
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
    """本地制裁/管制关键词数据库

    从 embargo_keywords.json 加载，支持热重载。
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
                "EmbargoDatabase 已加载: countries=%d ports=%d tickers=%d keywords=%d",
                len(self.sanctioned_countries),
                len(self.sanctioned_ports),
                len(self.restricted_ticker_prefixes),
                len(self.dual_use_keywords),
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
    """LangGraph 节点: RegGuard 出口管制检查

    在 demand_node 之后、supply_node 之前执行。
    命中制裁名单 → 设置 status=reg_denied，路由到 END。

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

    checker = SanctionChecker()
    result = checker.check(
        destination=destination,
        ticker_id=ticker_id,
        product_keywords=product_kw,
        category=category,
        raw_input=raw_input,
    )

    if not result["passed"]:
        # 广播 REG_DENIED 事件到 MarketDataBus
        _broadcast_reg_denied(ticker_id, destination, result["matched_rules"])

        # 加密审计日志
        _audit_reg_denied(destination, ticker_id, result)

        return {
            "reg_guard_result": result,
            "status": "reg_denied",
            "error": result["details"],
        }

    logger.info("RegGuard 通过: destination=%s ticker=%s", destination, ticker_id)
    return {
        "reg_guard_result": result,
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
