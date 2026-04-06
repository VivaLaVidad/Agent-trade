"""
monitor.heartbeat — AES-256-GCM 加密心跳监控
─────────────────────────────────────────────
职责：
  1. 每 10 秒采集进程存活指标（PID / CPU / 内存 / 活跃任务数）
  2. 将指标 JSON 用 AES-256-GCM 加密后 base64 编码
  3. 逐行追加写入 logs/heartbeat_YYYYMMDD.enc
  4. 提供 start() / stop() 异步生命周期方法，由 main.py lifespan 管控
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from datetime import datetime, timezone
from typing import Any

import psutil

from core.logger import get_logger
from core.security import get_cipher

logger = get_logger(__name__)

_INTERVAL_SECONDS: int = 10
_LOGS_DIR: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "logs",
)


class HeartbeatMonitor:
    """AES 加密心跳监控器

    后台异步任务每隔 ``_INTERVAL_SECONDS`` 秒向日志目录写入
    一行 base64(AES-256-GCM(json_payload)) 格式的心跳记录。
    """

    def __init__(self, logs_dir: str | None = None) -> None:
        self._logs_dir: str = os.path.normpath(logs_dir or _LOGS_DIR)
        self._task: asyncio.Task | None = None
        self._running: bool = False
        self._process: psutil.Process = psutil.Process(os.getpid())

    async def start(self) -> None:
        os.makedirs(self._logs_dir, exist_ok=True)
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("心跳监控已启动, interval=%ds, dir=%s", _INTERVAL_SECONDS, self._logs_dir)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("心跳监控已停止")

    async def _loop(self) -> None:
        while self._running:
            try:
                self._write_heartbeat()
            except Exception as exc:
                logger.error("心跳写入失败: %s", exc)
            await asyncio.sleep(_INTERVAL_SECONDS)

    def _collect_metrics(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        mem_info = self._process.memory_info()
        return {
            "ts": now.isoformat(),
            "pid": os.getpid(),
            "cpu_percent": self._process.cpu_percent(interval=0),
            "memory_mb": round(mem_info.rss / (1024 * 1024), 2),
            "threads": self._process.num_threads(),
            "uptime_s": round(time.time() - self._process.create_time(), 1),
        }

    def _write_heartbeat(self) -> None:
        import json

        payload: str = json.dumps(self._collect_metrics(), ensure_ascii=False)
        cipher = get_cipher()
        encrypted: bytes = cipher.encrypt_string(payload)
        line: str = base64.b64encode(encrypted).decode("ascii")

        today: str = datetime.now(timezone.utc).strftime("%Y%m%d")
        filepath: str = os.path.join(self._logs_dir, f"heartbeat_{today}.enc")

        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line + "\n")
