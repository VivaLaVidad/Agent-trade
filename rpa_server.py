"""
rpa_server.py — RPA gRPC 服务独立进程入口
──────────────────────────────────────────
启动方式：python rpa_server.py
监听地址：127.0.0.1:50051（仅本机）
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from rpa_engine.abstract_layer.server import serve

if __name__ == "__main__":
    asyncio.run(serve())
