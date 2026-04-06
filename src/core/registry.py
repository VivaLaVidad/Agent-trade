"""
core.registry — 暗箱模块注册中心 (Service Locator)
───────────────────────────────────────────────────
职责：
  1. 以 Registry 模式统一注册 / 获取所有业务能力模块
  2. 自动发现 src/modules/*/ 包并调用其 register() 钩子
  3. 线程安全、懒实例化
"""

from __future__ import annotations

import importlib
import pkgutil
import threading
from typing import Any, Callable, TypeVar

from core.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class ModuleRegistry:
    """全局模块注册表 —— 单例模式

    所有暗箱业务模块（LeadMiner / EmailCampaigner / DocGenerator /
    StealthLogger / HardwareLicense）在启动时通过各自包的
    ``register()`` 钩子注入本注册表，运行时通过 ``get()`` 按名获取。
    """

    _instance: ModuleRegistry | None = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> ModuleRegistry:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    inst = super().__new__(cls)
                    inst._factories: dict[str, Callable] = {}
                    inst._instances: dict[str, Any] = {}
                    cls._instance = inst
        return cls._instance

    def register(self, name: str, factory: Callable[..., Any]) -> None:
        """注册模块工厂（类或可调用对象）"""
        with self._lock:
            self._factories[name] = factory
            logger.info("模块已注册: %s → %s", name, factory.__name__)

    def get(self, name: str, **kwargs: Any) -> Any:
        """按名获取模块实例（懒实例化，同名仅创建一次）"""
        if name not in self._instances:
            with self._lock:
                if name not in self._instances:
                    factory = self._factories.get(name)
                    if factory is None:
                        raise KeyError(f"未注册的模块: {name}")
                    self._instances[name] = factory(**kwargs)
        return self._instances[name]

    def list_all(self) -> list[str]:
        """列出所有已注册的模块名"""
        return list(self._factories.keys())

    def auto_discover(self) -> None:
        """扫描 modules/ 包下的所有子包，调用各自的 register() 钩子"""
        try:
            import modules
        except ImportError:
            logger.warning("modules 包不存在，跳过自动发现")
            return

        for importer, modname, ispkg in pkgutil.iter_modules(
            modules.__path__, modules.__name__ + ".",
        ):
            if not ispkg:
                continue
            try:
                pkg = importlib.import_module(modname)
                hook = getattr(pkg, "register", None)
                if callable(hook):
                    hook(self)
                    logger.info("模块包已加载: %s", modname)
                else:
                    logger.warning("模块包 %s 缺少 register() 钩子，跳过", modname)
            except Exception as exc:
                logger.error("加载模块包 %s 失败: %s", modname, exc)

    def reset(self) -> None:
        """清空注册表（仅用于测试）"""
        with self._lock:
            self._factories.clear()
            self._instances.clear()
