"""
rpa_engine.abstract_layer.server — gRPC RPA 服务端
───────────────────────────────────────────────────
职责：
  1. 以独立进程运行，将 StealthBrowser 暴露为 gRPC 服务
  2. 监听 127.0.0.1:50051，仅接受本机调用
  3. 接收 TaskRequest，委托 StealthBrowser.execute_task() 执行
"""

from __future__ import annotations

import asyncio
import json
import time
from concurrent import futures
from typing import Any

import grpc

from rpa_engine.abstract_layer import rpa_pb2, rpa_pb2_grpc
from rpa_engine.browser_stealth import StealthBrowser
from core.logger import get_logger

logger = get_logger(__name__)

_LISTEN_ADDR: str = "127.0.0.1:50051"
_START_TIME: float = 0.0


class RPAServicer(rpa_pb2_grpc.RPAServiceServicer):
    """gRPC 服务实现 —— 将每个 RPC 调用委托给 StealthBrowser"""

    def __init__(self) -> None:
        self._browser: StealthBrowser | None = None
        self._lock = asyncio.Lock()

    async def _ensure_browser(self) -> StealthBrowser:
        if self._browser is None:
            async with self._lock:
                if self._browser is None:
                    self._browser = StealthBrowser()
                    await self._browser._launch()
                    logger.info("StealthBrowser 已在 gRPC 服务端启动")
        return self._browser

    async def ExecuteTask(
        self,
        request: rpa_pb2.TaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> rpa_pb2.TaskResponse:
        task_id: str = request.task_id
        task_type: str = request.task_type
        logger.info("gRPC ExecuteTask: id=%s type=%s", task_id, task_type)

        try:
            params: dict[str, Any] = json.loads(request.params_json)
            params["task_type"] = task_type

            browser = await self._ensure_browser()
            result: dict[str, Any] = await browser.execute_task(params)

            return rpa_pb2.TaskResponse(
                success=True,
                result_json=json.dumps(result, ensure_ascii=False),
                error="",
            )
        except Exception as exc:
            logger.error("gRPC ExecuteTask 失败: id=%s error=%s", task_id, exc)
            return rpa_pb2.TaskResponse(
                success=False,
                result_json="{}",
                error=str(exc),
            )

    async def Ping(
        self,
        request: rpa_pb2.PingRequest,
        context: grpc.aio.ServicerContext,
    ) -> rpa_pb2.PingResponse:
        return rpa_pb2.PingResponse(
            status="alive",
            uptime_seconds=time.time() - _START_TIME,
        )

    async def shutdown(self) -> None:
        if self._browser:
            await self._browser.close()
            self._browser = None


async def serve() -> None:
    """启动 gRPC 异步服务（阻塞直到收到终止信号）"""
    global _START_TIME
    _START_TIME = time.time()

    server = grpc.aio.server()
    servicer = RPAServicer()
    rpa_pb2_grpc.add_RPAServiceServicer_to_server(servicer, server)
    server.add_insecure_port(_LISTEN_ADDR)

    await server.start()
    logger.info("RPA gRPC 服务已启动: %s", _LISTEN_ADDR)

    try:
        await server.wait_for_termination()
    finally:
        await servicer.shutdown()
        logger.info("RPA gRPC 服务已关闭")
