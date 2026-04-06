"""audit_module — 审计与授权暗箱模块自动注册"""

from modules.audit_module.stealth_logger import StealthLogger
from modules.audit_module.hardware_license import LicenseManager


def register(registry) -> None:
    registry.register("stealth_logger", StealthLogger)
    registry.register("hardware_license", LicenseManager)
