"""
Pytest 全局夹具 — CI / 无 .env 环境下保证加密相关测试可运行。

AES_MASTER_KEY 未设置时，注入仅用于测试的 32 字节 hex 密钥，并清空
security 模块中对配置的 lru 缓存，避免先导入再设环境变量失效。
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 32 字节全零密钥的 hex 表示（仅测试用，禁止用于生产）
_DEFAULT_TEST_AES_HEX = "00" * 32


def pytest_configure(config) -> None:
    if not (os.environ.get("AES_MASTER_KEY") or "").strip():
        os.environ["AES_MASTER_KEY"] = _DEFAULT_TEST_AES_HEX
    try:
        from core import security

        security._settings.cache_clear()
        security.get_cipher.cache_clear()
    except Exception:
        pass
