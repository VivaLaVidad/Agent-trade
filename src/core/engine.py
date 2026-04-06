"""
core.engine — 主引擎进程编排器 (MainEngine)
─────────────────────────────────────────────
职责：
  1. 以子进程方式启动 FastAPI (uvicorn) + gRPC RPA Server
  2. stop() 先发 SIGTERM，再遍历子进程树彻底杀死 Playwright/Chromium 残留
  3. 提供 status() / get_metrics() 供控制面板实时读取
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Any

import psutil

from core.logger import get_logger

logger = get_logger(__name__)

_PROJECT_ROOT: str = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir),
)
_PYTHON: str = sys.executable


class MainEngine:
    """暗箱平台主引擎 —— 子进程生命周期管理器

    控制面板通过本类启动 / 停止后端服务，并轮询运行指标。
    """

    def __init__(self) -> None:
        self._api_proc: subprocess.Popen | None = None
        self._rpa_proc: subprocess.Popen | None = None
        self._start_time: float = 0.0

    @property
    def is_running(self) -> bool:
        return (
            self._api_proc is not None
            and self._api_proc.poll() is None
        )

    def start(self) -> dict[str, Any]:
        """启动 FastAPI + gRPC RPA Server 子进程

        Returns
        -------
        dict
            {"status": "started", "api_pid": int, "rpa_pid": int}
        """
        if self.is_running:
            return {"status": "already_running", "api_pid": self._api_proc.pid}

        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(_PROJECT_ROOT, "src")

        self._api_proc = subprocess.Popen(
            [_PYTHON, os.path.join(_PROJECT_ROOT, "main.py")],
            cwd=_PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        self._rpa_proc = subprocess.Popen(
            [_PYTHON, os.path.join(_PROJECT_ROOT, "rpa_server.py")],
            cwd=_PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

        self._start_time = time.time()
        logger.info(
            "引擎已启动: api_pid=%d rpa_pid=%d",
            self._api_proc.pid, self._rpa_proc.pid,
        )
        return {
            "status": "started",
            "api_pid": self._api_proc.pid,
            "rpa_pid": self._rpa_proc.pid,
        }

    def stop(self) -> dict[str, Any]:
        """停止所有子进程并清理 Playwright/Chromium 残留

        先发送终止信号，等待 5 秒后强杀所有残存子进程。

        Returns
        -------
        dict
            {"status": "stopped", "killed_pids": list[int]}
        """
        killed: list[int] = []

        for proc in (self._api_proc, self._rpa_proc):
            if proc is None or proc.poll() is not None:
                continue
            try:
                parent = psutil.Process(proc.pid)
                children = parent.children(recursive=True)

                if os.name == "nt":
                    proc.terminate()
                else:
                    os.kill(proc.pid, signal.SIGTERM)

                proc.wait(timeout=5)

                for child in children:
                    if child.is_running():
                        child.kill()
                        killed.append(child.pid)

            except psutil.NoSuchProcess:
                pass
            except subprocess.TimeoutExpired:
                proc.kill()
                killed.append(proc.pid)
            except Exception as exc:
                logger.error("停止子进程异常: %s", exc)

        self._kill_orphan_browsers()

        self._api_proc = None
        self._rpa_proc = None
        self._start_time = 0.0

        logger.info("引擎已停止, 清理 PID: %s", killed or "无残留")
        return {"status": "stopped", "killed_pids": killed}

    def _kill_orphan_browsers(self) -> None:
        """扫描并杀死所有孤儿 Chromium / Chrome 进程（Playwright 残留）"""
        targets = {"chromium", "chrome", "chrome.exe", "chromium.exe"}
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() in targets:
                    parent = proc.parent()
                    if parent is None or parent.pid == 1 or not parent.is_running():
                        proc.kill()
                        logger.info("已杀死孤儿浏览器进程: pid=%d", proc.info["pid"])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def status(self) -> dict[str, Any]:
        """获取引擎运行状态"""
        running = self.is_running
        return {
            "running": running,
            "api_pid": self._api_proc.pid if self._api_proc and running else None,
            "rpa_pid": self._rpa_proc.pid if self._rpa_proc and self._rpa_proc.poll() is None else None,
            "uptime_seconds": round(time.time() - self._start_time, 1) if running else 0,
        }

    def get_metrics(self) -> dict[str, Any]:
        """获取系统运行指标（CPU / 内存 / 任务队列）"""
        metrics: dict[str, Any] = {
            "cpu_percent": psutil.cpu_percent(interval=0),
            "memory_mb": round(psutil.virtual_memory().used / (1024 * 1024), 1),
            "memory_percent": psutil.virtual_memory().percent,
        }

        try:
            if not hasattr(self, "_recovery"):
                from database.task_recovery import TaskRecoveryManager
                self._recovery = TaskRecoveryManager()
            interrupted = self._recovery.get_interrupted_tasks()
            metrics["pending_tasks"] = len(interrupted)
        except Exception:
            metrics["pending_tasks"] = 0

        return metrics
