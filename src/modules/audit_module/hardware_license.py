"""
modules.audit_module.hardware_license — 硬件绑定许可证系统
─────────────────────────────────────────────────────────
职责：
  1. 基于 MachineAuth 机器指纹生成 AES 加密 .lic 许可文件
  2. 启动时验证许可证：机器绑定 + 有效期 + 功能门控
  3. 许可证无效或过期时硬阻断系统启动
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any

from core.logger import get_logger
from core.security import MachineAuth, get_cipher

logger = get_logger(__name__)

_LICENSE_PATH: str = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, os.pardir, "license.lic"),
)


@dataclass
class License:
    """许可证数据结构"""
    machine_id: str
    issued_at: str
    expires_at: str
    licensee: str = "internal"
    max_leads_per_day: int = 100
    features_enabled: list[str] = field(default_factory=lambda: [
        "lead_miner", "email_campaigner", "doc_generator",
        "stealth_logger", "hardware_license",
    ])


class LicenseManager:
    """硬件绑定许可证管理器

    生成、加载、验证 AES-256-GCM 加密的 .lic 许可文件。
    系统启动时调用 ``validate()``，不通过则抛出 ``LicenseError``。
    """

    def __init__(self, license_path: str | None = None) -> None:
        self._path: str = license_path or _LICENSE_PATH

    async def execute(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Registry 兼容接口"""
        action = params.get("action", "validate")
        if action == "generate":
            days = params.get("days", 365)
            self.generate_license_file(valid_days=days)
            return {"status": "generated", "path": self._path}
        elif action == "validate":
            lic = self.validate()
            return {"status": "valid", "expires_at": lic.expires_at}
        return {"status": "unknown_action"}

    def generate_license_file(
        self,
        valid_days: int = 365,
        licensee: str = "internal",
        max_leads_per_day: int = 100,
    ) -> str:
        """为当前机器生成加密许可文件

        Parameters
        ----------
        valid_days : int
            有效天数
        licensee : str
            被授权方名称
        max_leads_per_day : int
            每日最大挖掘数

        Returns
        -------
        str
            许可文件路径
        """
        now = datetime.now(timezone.utc)
        lic = License(
            machine_id=MachineAuth.get_machine_id(),
            issued_at=now.isoformat(),
            expires_at=(now + timedelta(days=valid_days)).isoformat(),
            licensee=licensee,
            max_leads_per_day=max_leads_per_day,
        )

        plaintext = json.dumps(asdict(lic), ensure_ascii=False)
        cipher = get_cipher()
        encrypted = cipher.encrypt_string(plaintext)
        encoded = base64.b64encode(encrypted).decode("ascii")

        with open(self._path, "w", encoding="utf-8") as f:
            f.write(encoded)

        logger.info(
            "许可证已生成: machine=%s…  expires=%s  path=%s",
            lic.machine_id[:8], lic.expires_at[:10], self._path,
        )
        return self._path

    def load_license_file(self) -> License:
        """从磁盘加载并解密许可证"""
        if not os.path.exists(self._path):
            raise LicenseError("许可证文件不存在，请先生成: LicenseManager().generate_license_file()")

        with open(self._path, "r", encoding="utf-8") as f:
            encoded = f.read().strip()

        cipher = get_cipher()
        encrypted = base64.b64decode(encoded)
        plaintext = cipher.decrypt_string(encrypted)
        data = json.loads(plaintext)
        return License(**data)

    def validate(self) -> License:
        """验证许可证有效性（机器绑定 + 有效期）

        Returns
        -------
        License
            验证通过的许可证对象

        Raises
        ------
        LicenseError
            许可证无效、过期或机器不匹配
        """
        lic = self.load_license_file()

        current_machine = MachineAuth.get_machine_id()
        if lic.machine_id != current_machine:
            raise LicenseError(
                f"机器码不匹配: 许可证绑定={lic.machine_id[:8]}…  "
                f"当前机器={current_machine[:8]}…"
            )

        now = datetime.now(timezone.utc)
        expires = datetime.fromisoformat(lic.expires_at)
        if now > expires:
            raise LicenseError(f"许可证已过期: {lic.expires_at}")

        days_left = (expires - now).days
        logger.info("许可证验证通过: licensee=%s  剩余 %d 天", lic.licensee, days_left)

        if days_left <= 30:
            logger.warning("许可证即将过期，剩余 %d 天", days_left)

        return lic

    def is_feature_enabled(self, feature: str) -> bool:
        """检查某功能是否在许可范围内"""
        try:
            lic = self.load_license_file()
            return feature in lic.features_enabled
        except (LicenseError, Exception):
            return False


class LicenseError(Exception):
    """许可证验证失败异常"""
    pass
