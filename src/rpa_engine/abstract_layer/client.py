"""
rpa_engine.abstract_layer.client — gRPC RPA 客户端（含指数退避重试）
──────────────────────────────────────────────────────────────────
职责：
  1. 封装 gRPC Stub，对外提供 async execute_task() 接口
  2. 所有网络调用均使用 tenacity 指数退避重试
     - wait: 1s → 2s → 4s → 8s … 上限 60s
     - 最多重试 5 次
     - 仅在 UNAVAILABLE / DEADLINE_EXCEEDED 时重试
  3. 业务层（agents / workflow_graph）只依赖此客户端，不直接碰 Playwright
"""

from __future__ import annotations

import json
from typing import Any

import grpc
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from rpa_engine.abstract_layer import rpa_pb2, rpa_pb2_grpc
from core.logger import get_logger

logger = get_logger(__name__)

_RPA_SERVER_ADDR: str = "127.0.0.1:50051"
_MAX_RETRIES: int = 5
_BACKOFF_MIN: int = 1
_BACKOFF_MAX: int = 60


def _is_retryable_grpc_error(exc: BaseException) -> bool:
    """判断 gRPC 错误是否值得重试"""
    if not isinstance(exc, grpc.aio.AioRpcError):
        return False
    retryable_codes = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED}
    return exc.code() in retryable_codes


class RPAClient:
    """gRPC RPA 客户端 —— 工作流编排层的唯一 RPA 调用入口

    通过 gRPC 连接独立的 RPA 服务进程，所有调用自带
    指数退避重试，屏蔽瞬时网络 / 进程故障。
    """

    def __init__(self, server_addr: str = _RPA_SERVER_ADDR) -> None:
        self._addr: str = server_addr
        self._channel: grpc.aio.Channel | None = None
        self._stub: rpa_pb2_grpc.RPAServiceStub | None = None

    async def _ensure_channel(self) -> rpa_pb2_grpc.RPAServiceStub:
        if self._stub is None:
            self._channel = grpc.aio.insecure_channel(self._addr)
            self._stub = rpa_pb2_grpc.RPAServiceStub(self._channel)
        return self._stub

    @retry(
        stop=stop_after_attempt(_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=_BACKOFF_MIN, max=_BACKOFF_MAX),
        retry=retry_if_exception(_is_retryable_grpc_error),
        before_sleep=lambda rs: logger.warning(
            "RPA gRPC 调用失败，%ds 后第 %d 次重试…",
            rs.next_action.sleep, rs.attempt_number,
        ),
        reraise=True,
    )
    async def execute_task(
        self,
        task_id: str,
        task_type: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """向 RPA 服务发送任务并返回结果

        Parameters
        ----------
        task_id : str
            任务唯一标识
        task_type : str
            任务类型（login / send_email / scrape_leads）
        params : dict
            任务参数（不含 task_type，由本方法填充）

        Returns
        -------
        dict[str, Any]
            RPA 执行结果

        Raises
        ------
        grpc.aio.AioRpcError
            重试耗尽后仍然失败
        RuntimeError
            RPA 服务端返回 success=False
        """
        stub = await self._ensure_channel()

        request = rpa_pb2.TaskRequest(
            task_id=task_id,
            task_type=task_type,
            params_json=json.dumps(params, ensure_ascii=False),
        )

        response: rpa_pb2.TaskResponse = await stub.ExecuteTask(request)

        if not response.success:
            raise RuntimeError(f"RPA 任务执行失败: {response.error}")

        result: dict[str, Any] = json.loads(response.result_json)
        logger.info("RPA 任务完成: id=%s type=%s", task_id, task_type)
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_retryable_grpc_error),
        reraise=True,
    )
    async def ping(self) -> dict[str, Any]:
        """检测 RPA 服务存活状态"""
        stub = await self._ensure_channel()
        response = await stub.Ping(rpa_pb2.PingRequest())
        return {"status": response.status, "uptime_seconds": response.uptime_seconds}

    async def close(self) -> None:
        if self._channel:
            await self._channel.close()
            self._channel = None
            self._stub = None
