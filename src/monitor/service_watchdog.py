"""
monitor.service_watchdog — RPA Worker 容灾监控守卫
──────────────────────────────────────────────────
职责：
  1. 持续监控 rpa_engine gRPC Worker 的内存占用与网络健康度
  2. 通信超时（gRPC Ping 失败 3 次）→ 自动重启 Worker 进程
  3. 内存泄漏（超过 500MB）→ 立即触发重启
  4. 所有重启操作记录审计日志
  5. 支持 Docker / systemctl / 直接进程重启三种模式

监控循环::

    while running:
        sleep(interval)
        check_memory() → 超限 → restart()
        check_grpc_ping() → 超时 → restart()
        report_metrics()
"""

from __future__ import annotations

import asyncio
import os
import platform
import subprocess
import time
from typing import Any, Literal

import psutil

from core.logger import get_logger

logger = get_logger(__name__)

RestartMode = Literal["process", "docker", "systemctl"]

_DEFAULT_CHECK_INTERVAL: int = 15       # 秒
_MEMORY_LIMIT_MB: int = 500             # 内存上限 MB
_PING_TIMEOUT_SECONDS: float = 5.0      # gRPC Ping 超时
_MAX_CONSECUTIVE_FAILURES: int = 3      # 连续失败次数触发重启
_RESTART_COOLDOWN: int = 30             # 重启冷却期（秒）


class ServiceWatchdog:
    """RPA Worker 容灾监控守卫

    持续监控 gRPC Worker 进程的健康状态，
    在检测到异常时自动执行重启恢复。

    Parameters
    ----------
    rpa_server_addr : str
        gRPC Worker 地址
    restart_mode : RestartMode
        重启方式: "process" | "docker" | "systemctl"
    service_name : str
        Docker 容器名或 systemd 服务名
    check_interval : int
        检查间隔（秒）
    memory_limit_mb : int
        内存上限（MB）
    """

    def __init__(
        self,
        rpa_server_addr: str = "127.0.0.1:50051",
        restart_mode: RestartMode = "process",
        service_name: str = "rpa-worker",
        check_interval: int = _DEFAULT_CHECK_INTERVAL,
        memory_limit_mb: int = _MEMORY_LIMIT_MB,
    ) -> None:
        self._addr = rpa_server_addr
        self._restart_mode = restart_mode
        self._service_name = service_name
        self._interval = check_interval
        self._mem_limit = memory_limit_mb
        self._running = False
        self._task: asyncio.Task | None = None
        self._consecutive_failures = 0
        self._last_restart_ts: float = 0
        self._total_restarts = 0
        self._metrics: dict[str, Any] = {}

    async def start(self) -> None:
        """启动监控循环"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(
            "ServiceWatchdog 已启动: addr=%s mode=%s interval=%ds mem_limit=%dMB",
            self._addr, self._restart_mode, self._interval, self._mem_limit,
        )

    async def stop(self) -> None:
        """停止监控循环"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ServiceWatchdog 已停止")

    def get_metrics(self) -> dict[str, Any]:
        """获取最新监控指标"""
        return {
            **self._metrics,
            "total_restarts": self._total_restarts,
            "consecutive_failures": self._consecutive_failures,
            "watchdog_running": self._running,
        }

    async def _watch_loop(self) -> None:
        """主监控循环"""
        while self._running:
            try:
                await asyncio.sleep(self._interval)

                # 1. 内存检查
                mem_ok = await self._check_memory()

                # 2. gRPC 健康检查
                ping_ok = await self._check_grpc_ping()

                # 3. 更新指标
                self._metrics.update({
                    "last_check_ts": time.time(),
                    "memory_ok": mem_ok,
                    "ping_ok": ping_ok,
                })

                if mem_ok and ping_ok:
                    self._consecutive_failures = 0
                else:
                    self._consecutive_failures += 1

                # 4. 触发重启判定
                if self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    await self._trigger_restart(
                        reason=f"连续 {self._consecutive_failures} 次健康检查失败"
                    )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Watchdog 循环异常: %s", exc)

    async def _check_memory(self) -> bool:
        """检查 RPA Worker 进程内存占用"""
        try:
            rpa_procs = []
            for proc in psutil.process_iter(["pid", "name", "memory_info", "cmdline"]):
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "rpa_server" in cmdline or "rpa_engine" in cmdline:
                    rpa_procs.append(proc)

            if not rpa_procs:
                self._metrics["rpa_memory_mb"] = 0
                return True  # 进程不存在不算内存问题

            total_mb = sum(
                (p.info.get("memory_info") or p.memory_info()).rss / (1024 * 1024)
                for p in rpa_procs
            )
            self._metrics["rpa_memory_mb"] = round(total_mb, 1)

            if total_mb > self._mem_limit:
                logger.warning(
                    "RPA Worker 内存超限: %.1fMB > %dMB — 触发重启",
                    total_mb, self._mem_limit,
                )
                await self._trigger_restart(
                    reason=f"内存泄漏 {total_mb:.0f}MB > {self._mem_limit}MB"
                )
                return False

            return True

        except Exception as exc:
            logger.debug("内存检查异常: %s", exc)
            return True

    async def _check_grpc_ping(self) -> bool:
        """通过 gRPC Ping 检查 Worker 存活"""
        try:
            from rpa_engine.abstract_layer.client import RPAClient

            client = RPAClient(server_addr=self._addr)
            try:
                result = await asyncio.wait_for(
                    client.ping(),
                    timeout=_PING_TIMEOUT_SECONDS,
                )
                self._metrics["rpa_uptime_seconds"] = result.get("uptime_seconds", 0)
                return result.get("status") == "alive"
            finally:
                await client.close()

        except asyncio.TimeoutError:
            logger.warning("gRPC Ping 超时 (%.1fs)", _PING_TIMEOUT_SECONDS)
            return False
        except Exception as exc:
            logger.debug("gRPC Ping 失败: %s", exc)
            return False

    async def _trigger_restart(self, reason: str) -> None:
        """执行 Worker 重启"""
        now = time.time()
        if now - self._last_restart_ts < _RESTART_COOLDOWN:
            logger.info("重启冷却中 (%ds)，跳过", _RESTART_COOLDOWN)
            return

        logger.warning("触发 RPA Worker 重启: reason=%s mode=%s", reason, self._restart_mode)

        try:
            if self._restart_mode == "docker":
                await self._restart_docker()
            elif self._restart_mode == "systemctl":
                await self._restart_systemctl()
            else:
                await self._restart_process()

            self._last_restart_ts = time.time()
            self._total_restarts += 1
            self._consecutive_failures = 0

            # 审计日志
            self._log_restart_audit(reason)

            logger.info(
                "RPA Worker 重启完成: total_restarts=%d", self._total_restarts,
            )

        except Exception as exc:
            logger.error("RPA Worker 重启失败: %s", exc)

    async def _restart_docker(self) -> None:
        """Docker 容器重启"""
        cmd = f"docker restart {self._service_name}"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"docker restart 失败: {stderr.decode()}")

    async def _restart_systemctl(self) -> None:
        """systemd 服务重启"""
        cmd = f"systemctl restart {self._service_name}"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"systemctl restart 失败: {stderr.decode()}")

    async def _restart_process(self) -> None:
        """直接进程重启（kill + re-spawn）"""
        # Kill existing RPA processes
        for proc in psutil.process_iter(["pid", "cmdline"]):
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if "rpa_server" in cmdline:
                try:
                    proc.kill()
                    logger.info("已终止 RPA 进程: pid=%d", proc.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        await asyncio.sleep(2)

        # Re-spawn
        python_exe = "python" if platform.system() == "Windows" else "python3"
        project_root = os.path.normpath(
            os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
        )
        rpa_script = os.path.join(project_root, "rpa_server.py")

        if os.path.exists(rpa_script):
            subprocess.Popen(
                [python_exe, rpa_script],
                cwd=project_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if platform.system() == "Windows"
                    else 0
                ),
            )
            logger.info("RPA Worker 已重新启动: %s", rpa_script)

    def _log_restart_audit(self, reason: str) -> None:
        """记录重启审计日志"""
        try:
            from modules.audit_module.stealth_logger import StealthLogger
            audit = StealthLogger()
            audit.log_event(
                module="service_watchdog",
                action="rpa_worker_restart",
                detail={
                    "reason": reason,
                    "restart_mode": self._restart_mode,
                    "total_restarts": self._total_restarts,
                    "memory_mb": self._metrics.get("rpa_memory_mb", 0),
                },
                operator="watchdog_auto",
            )
        except Exception:
            pass  # 审计失败不影响重启流程
