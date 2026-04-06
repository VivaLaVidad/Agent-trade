"""
core.agent_context — 统一数据交换总线 (AgentContext)
────────────────────────────────────────────────────
职责：
  1. 聚合所有基础设施引用（加密器 / 恢复管理器 / RPA 客户端 / 注册表）
  2. 所有业务模块通过 AgentContext 访问基础设施，杜绝硬编码依赖
  3. 提供 build() 工厂方法一键组装
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.logger import get_logger
from core.registry import ModuleRegistry
from core.security import AESCipher, get_cipher

logger = get_logger(__name__)


@dataclass
class AgentContext:
    """暗箱平台统一上下文 —— 模块间数据交换的唯一通道

    所有业务模块的 ``execute(ctx, params)`` 方法接收本对象，
    通过它获取加密器、注册表、恢复管理器等基础设施引用，
    而非自行 import。

    Attributes
    ----------
    registry : ModuleRegistry
        全局模块注册表
    cipher : AESCipher
        AES-256-GCM 加密器实例
    recovery : Any
        TaskRecoveryManager 实例（断电恢复）
    heartbeat : Any
        HeartbeatMonitor 实例（心跳监控）
    rpa_client : Any
        RPAClient gRPC 客户端实例（可选，控制面板模式下为 None）
    session_id : str
        当前会话标识
    shared : dict
        模块间共享的临时数据字典
    """

    registry: ModuleRegistry = field(default_factory=ModuleRegistry)
    cipher: AESCipher = field(default_factory=get_cipher)
    recovery: Any = None
    heartbeat: Any = None
    rpa_client: Any = None
    session_id: str = ""
    shared: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        recovery: Any = None,
        heartbeat: Any = None,
        rpa_client: Any = None,
    ) -> AgentContext:
        """工厂方法 —— 从当前环境一键组装完整上下文

        Parameters
        ----------
        recovery : TaskRecoveryManager | None
            断电恢复管理器
        heartbeat : HeartbeatMonitor | None
            心跳监控器
        rpa_client : RPAClient | None
            gRPC RPA 客户端

        Returns
        -------
        AgentContext
            已装配好所有基础设施引用的上下文实例
        """
        registry = ModuleRegistry()
        ctx = cls(
            registry=registry,
            cipher=get_cipher(),
            recovery=recovery,
            heartbeat=heartbeat,
            rpa_client=rpa_client,
        )
        registry.auto_discover()
        logger.info(
            "AgentContext 已构建, 已注册模块: %s",
            ", ".join(registry.list_all()) or "(无)",
        )
        return ctx

    def get_module(self, name: str) -> Any:
        """便捷方法 —— 从注册表获取模块实例"""
        return self.registry.get(name)
