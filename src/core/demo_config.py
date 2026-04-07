"""
core.demo_config — IS_DEMO_MODE 环境变量读取
─────────────────────────────────────────────
当 IS_DEMO_MODE=true 时，RPA 层（Playwright / gRPC）的物理操作
将被拦截并替换为虚拟执行，其余模块（ArbitrageEvaluator、
DocuForge、LedgerService）不受影响，始终 100% 真实运行。
"""

from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def is_demo_mode() -> bool:
    """读取 IS_DEMO_MODE 环境变量，返回布尔值。"""
    return os.getenv("IS_DEMO_MODE", "false").strip().lower() in ("true", "1", "yes")
