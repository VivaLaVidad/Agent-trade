"""
modules.audit_module.stealth_logger — 加密操作审计日志
──────────────────────────────────────────────────────
职责：
  1. 记录所有业务操作的审计事件（线索挖掘 / 邮件发送 / 文档生成）
  2. 事件 JSON → AES-256-GCM 加密 → base64 逐行写入 logs/audit_YYYYMMDD.enc
  3. 提供定时销毁：安全覆写 + 删除超过保留期的历史日志
  4. 与 monitor/heartbeat.py（系统健康）互补，本模块聚焦业务级审计
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any

from core.logger import get_logger
from core.security import get_cipher

logger = get_logger(__name__)

_LOGS_DIR: str = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, os.pardir, "logs"),
)
_DEFAULT_RETENTION_DAYS: int = 30


class StealthLogger:
    """加密审计日志引擎

    每个业务操作调用 ``log_event()`` 写入一条不可逆加密审计记录。
    ``purge_older_than()`` 安全销毁过期日志（先覆写再删除）。
    """

    def __init__(self, logs_dir: str | None = None) -> None:
        self._logs_dir: str = logs_dir or _LOGS_DIR
        os.makedirs(self._logs_dir, exist_ok=True)

    async def execute(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Registry 兼容接口 —— 审计事件写入"""
        event = params.get("event", {})
        self.log_event(
            module=event.get("module", "unknown"),
            action=event.get("action", "unknown"),
            detail=event.get("detail", {}),
            operator=event.get("operator", "system"),
        )
        return {"status": "logged"}

    def log_event(
        self,
        module: str,
        action: str,
        detail: dict[str, Any] | None = None,
        operator: str = "system",
    ) -> None:
        """写入一条加密审计记录

        Parameters
        ----------
        module : str
            来源模块（lead_miner / email_campaigner / doc_generator）
        action : str
            操作类型（execute / send / generate / login）
        detail : dict | None
            操作详情（会被完整加密，不泄露到磁盘明文）
        operator : str
            操作者标识
        """
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "module": module,
            "action": action,
            "operator": operator,
            "detail": detail or {},
        }

        try:
            plaintext: str = json.dumps(payload, ensure_ascii=False)
            cipher = get_cipher()
            encrypted: bytes = cipher.encrypt_string(plaintext)
            line: str = base64.b64encode(encrypted).decode("ascii")

            today: str = datetime.now(timezone.utc).strftime("%Y%m%d")
            filepath: str = os.path.join(self._logs_dir, f"audit_{today}.enc")

            with open(filepath, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as exc:
            logger.error("审计日志写入失败: %s", exc)

    def read_events(self, date_str: str) -> list[dict[str, Any]]:
        """解密读取指定日期的审计日志（仅限内部诊断）"""
        filepath = os.path.join(self._logs_dir, f"audit_{date_str}.enc")
        if not os.path.exists(filepath):
            return []

        events: list[dict[str, Any]] = []
        cipher = get_cipher()
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    encrypted = base64.b64decode(line)
                    plaintext = cipher.decrypt_string(encrypted)
                    events.append(json.loads(plaintext))
                except Exception:
                    continue
        return events

    def purge_older_than(self, days: int = _DEFAULT_RETENTION_DAYS) -> int:
        """安全销毁过期审计日志（覆写随机数据后删除）

        Parameters
        ----------
        days : int
            保留天数，超过此期限的文件将被安全销毁

        Returns
        -------
        int
            已销毁的文件数
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        destroyed = 0

        for filename in os.listdir(self._logs_dir):
            if not filename.startswith("audit_") or not filename.endswith(".enc"):
                continue

            date_part = filename.replace("audit_", "").replace(".enc", "")
            try:
                file_date = datetime.strptime(date_part, "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            if file_date >= cutoff:
                continue

            filepath = os.path.join(self._logs_dir, filename)
            try:
                file_size = os.path.getsize(filepath)
                with open(filepath, "wb") as f:
                    f.write(secrets.token_bytes(file_size))
                    f.flush()
                    os.fsync(f.fileno())
                os.remove(filepath)
                destroyed += 1
                logger.info("审计日志已安全销毁: %s", filename)
            except Exception as exc:
                logger.error("审计日志销毁失败: %s — %s", filename, exc)

        return destroyed
