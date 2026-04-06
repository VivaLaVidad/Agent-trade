"""
modules.audit_module.compliance_gateway — 暗箱合规网关
──────────────────────────────────────────────────────
职责：
  1. 所有业务流水必须先经过 AuditModule 加密后，方可进行外部同步
  2. 严禁在飞书/外部渠道上传原始报价明细或客户敏感信息
  3. 提供 sanitize() 方法：剥离敏感字段，仅保留非敏感摘要
  4. 提供 encrypt_and_log() 方法：加密原始数据 + 写入审计日志
  5. FeishuSync 等外部同步器必须通过本网关获取脱敏数据

暗箱模式原则::

    原始数据 → encrypt_and_log() → 加密存储（本地审计日志）
                                  ↓
                            sanitize() → 脱敏摘要 → FeishuSync / Webhook
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from core.logger import get_logger
from core.security import get_cipher

logger = get_logger(__name__)

# 敏感字段模式（正则匹配 key 名）
_SENSITIVE_PATTERNS: list[str] = [
    r"email",
    r"phone",
    r"contact",
    r"password",
    r"secret",
    r"token",
    r"bank",
    r"account",
    r"card",
    r"ssn",
    r"passport",
    r"address",
    r"pricing_audit_trail",
    r"signature",
    r"unit_price_rmb",
    r"base_price",
    r"adjusted_price",
    r"decision_logic",
    r"market_snapshot",
    r"counter_offer",
    r"buyer_offer",
    r"seller_offer",
]

_SENSITIVE_RE = re.compile(
    "|".join(f"({p})" for p in _SENSITIVE_PATTERNS),
    re.IGNORECASE,
)


class ComplianceGateway:
    """暗箱合规网关 —— 所有外部数据同步的唯一出口

    确保：
    - 原始报价明细、客户敏感信息永远不会以明文形式离开系统
    - 所有外部同步仅传输脱敏摘要
    - 原始数据加密后存入本地审计日志
    """

    def __init__(self) -> None:
        self._audit_logger = None

    def _get_audit_logger(self):
        if self._audit_logger is None:
            from modules.audit_module.stealth_logger import StealthLogger
            self._audit_logger = StealthLogger()
        return self._audit_logger

    def encrypt_and_log(
        self,
        module: str,
        action: str,
        raw_data: dict[str, Any],
        operator: str = "system",
    ) -> str:
        """加密原始数据并写入审计日志

        Parameters
        ----------
        module : str
            来源模块
        action : str
            操作类型
        raw_data : dict
            原始业务数据（含敏感信息）
        operator : str
            操作者

        Returns
        -------
        str
            审计记录的 SHA256 摘要（可用于关联查询）
        """
        # 1. 计算数据指纹
        data_json = json.dumps(raw_data, ensure_ascii=False, sort_keys=True, default=str)
        data_hash = hashlib.sha256(data_json.encode("utf-8")).hexdigest()

        # 2. 写入加密审计日志
        self._get_audit_logger().log_event(
            module=module,
            action=action,
            detail={
                "data_hash": data_hash,
                "data_size": len(data_json),
                **raw_data,
            },
            operator=operator,
        )

        logger.debug(
            "合规网关: 已加密记录 module=%s action=%s hash=%s",
            module, action, data_hash[:16],
        )
        return data_hash

    def sanitize(
        self,
        data: dict[str, Any],
        context: str = "external_sync",
    ) -> dict[str, Any]:
        """脱敏处理 —— 剥离所有敏感字段，仅保留非敏感摘要

        Parameters
        ----------
        data : dict
            原始业务数据
        context : str
            脱敏上下文标识

        Returns
        -------
        dict
            脱敏后的安全数据（可安全传输至飞书等外部渠道）
        """
        sanitized = self._deep_sanitize(data)
        sanitized["_compliance"] = {
            "sanitized": True,
            "context": context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gateway": "ComplianceGateway_v1",
        }
        return sanitized

    def prepare_for_feishu(
        self,
        trade_event: dict[str, Any],
        event_type: str = "trade_update",
    ) -> dict[str, Any]:
        """为飞书同步准备脱敏数据

        先加密原始数据到审计日志，再返回脱敏摘要。

        Parameters
        ----------
        trade_event : dict
            原始交易事件数据
        event_type : str
            事件类型

        Returns
        -------
        dict
            脱敏后的飞书消息 payload
        """
        # 1. 加密存储原始数据
        data_hash = self.encrypt_and_log(
            module="feishu_sync",
            action=event_type,
            raw_data=trade_event,
        )

        # 2. 生成脱敏摘要
        safe_data = self.sanitize(trade_event, context="feishu")

        # 3. 构建飞书消息
        return {
            "event_type": event_type,
            "data_hash": data_hash,
            "summary": safe_data,
            "note": "原始数据已加密存储，此处仅为脱敏摘要",
        }

    def _deep_sanitize(self, obj: Any, depth: int = 0) -> Any:
        """递归脱敏"""
        if depth > 10:
            return "[DEPTH_LIMIT]"

        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                if _SENSITIVE_RE.search(key):
                    # 敏感字段 → 替换为掩码
                    if isinstance(value, str):
                        result[key] = self._mask_string(value)
                    elif isinstance(value, (int, float)):
                        result[key] = "***"
                    elif isinstance(value, dict):
                        result[key] = {"_redacted": True, "keys": list(value.keys())}
                    elif isinstance(value, list):
                        result[key] = f"[{len(value)} items redacted]"
                    else:
                        result[key] = "[REDACTED]"
                else:
                    result[key] = self._deep_sanitize(value, depth + 1)
            return result

        elif isinstance(obj, list):
            return [self._deep_sanitize(item, depth + 1) for item in obj[:20]]

        elif isinstance(obj, str) and len(obj) > 200:
            return obj[:50] + "...[TRUNCATED]"

        return obj

    @staticmethod
    def _mask_string(value: str) -> str:
        """字符串掩码：保留首尾各 2 字符"""
        if len(value) <= 4:
            return "****"
        return value[:2] + "*" * min(len(value) - 4, 8) + value[-2:]


# 全局单例
_gateway: ComplianceGateway | None = None


def get_compliance_gateway() -> ComplianceGateway:
    """获取全局合规网关单例"""
    global _gateway
    if _gateway is None:
        _gateway = ComplianceGateway()
    return _gateway
