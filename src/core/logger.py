"""
core.logger — 脱敏日志输出
─────────────────────────────────
职责边界：
  1. 统一日志格式 (JSON structured logging)
  2. 自动脱敏敏感字段（手机号、邮箱、身份证、API Key 等）
  3. 提供全局 get_logger 入口
"""

from __future__ import annotations

import logging
import re
import sys
from copy import deepcopy
from typing import Any

import orjson

# ─── 脱敏正则 ────────────────────────────────────────────────
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"1[3-9]\d{9}"), lambda m: m.group()[:3] + "****" + m.group()[-4:]),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), lambda m: m.group()[:2] + "****@" + m.group().split("@")[-1]),
    (re.compile(r"\d{6}(18|19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx]"),
     lambda m: m.group()[:6] + "********" + m.group()[-4:]),
    (re.compile(r"(?i)(sk-|ak-|key[_-]?)[a-zA-Z0-9]{16,}"),
     lambda m: m.group()[:6] + "****"),
    (re.compile(r"\d{16,19}"),
     lambda m: m.group()[:4] + " **** **** " + m.group()[-4:]),
]

_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "access_key", "private_key", "credential", "authorization",
    "phone", "mobile", "email", "id_card", "bank_card",
})


def sanitize_text(text: str) -> str:
    """对字符串进行正则脱敏"""
    for pattern, replacer in _PATTERNS:
        text = pattern.sub(replacer, text)
    return text


def sanitize_dict(data: dict, *, _depth: int = 0) -> dict:
    """递归脱敏字典中的敏感字段"""
    if _depth > 10:
        return {"__truncated__": True}

    result = {}
    for k, v in data.items():
        key_lower = k.lower()
        if any(sk in key_lower for sk in _SENSITIVE_KEYS):
            result[k] = "***REDACTED***"
        elif isinstance(v, dict):
            result[k] = sanitize_dict(v, _depth=_depth + 1)
        elif isinstance(v, str):
            result[k] = sanitize_text(v)
        elif isinstance(v, list):
            result[k] = [
                sanitize_dict(i, _depth=_depth + 1) if isinstance(i, dict)
                else sanitize_text(i) if isinstance(i, str)
                else i
                for i in v
            ]
        else:
            result[k] = v
    return result


# ─── JSON Formatter ──────────────────────────────────────────
class _SanitizedJsonFormatter(logging.Formatter):

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        log_entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "module": record.module,
            "msg": sanitize_text(message),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exc"] = self.formatException(record.exc_info)
        return orjson.dumps(log_entry).decode()


# ─── Logger Factory ──────────────────────────────────────────
_formatter = _SanitizedJsonFormatter()
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_formatter)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """获取带脱敏能力的 Logger 实例"""
    log = logging.getLogger(f"tradestealth.{name}")
    if not log.handlers:
        log.addHandler(_handler)
        log.setLevel(level)
        log.propagate = False
    return log
