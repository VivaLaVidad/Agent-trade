"""
database.task_recovery — SQLite 断电恢复管理器
───────────────────────────────────────────────
职责：
  1. 在 db/task_status.db 中持久化邮件任务的执行状态
  2. 进程意外终止时，status=running 的记录即为中断任务
  3. 重启后 recover_on_startup() 自动检测并返回待恢复任务列表
  4. 与 WorkflowOrchestrator 集成：register → execute → complete/fail
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

_DB_DIR: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "db",
)
_DB_PATH: str = os.path.join(_DB_DIR, "task_status.db")

_CREATE_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS task_status (
    task_id     TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    task_type   TEXT NOT NULL DEFAULT 'email_workflow',
    status      TEXT NOT NULL DEFAULT 'pending',
    params_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    started_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


class TaskRecoveryManager:
    """SQLite 任务状态持久化管理器

    使用标准库 sqlite3（同步），无需异步驱动。
    所有写操作使用 WAL 模式以降低锁竞争。
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path: str = db_path or _DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.commit()
        logger.info("任务恢复数据库就绪: %s", self._db_path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def register_task(
        self,
        task_id: str,
        session_id: str,
        task_type: str = "email_workflow",
        params: dict[str, Any] | None = None,
    ) -> None:
        """在执行前注册任务（status=running）"""
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO task_status
                    (task_id, session_id, task_type, status, params_json, started_at, updated_at)
                VALUES (?, ?, ?, 'running', ?, ?, ?)
                """,
                (task_id, session_id, task_type, json.dumps(params or {}), now, now),
            )
            conn.commit()
        logger.info("任务已注册: id=%s session=%s", task_id, session_id)

    def complete_task(self, task_id: str, result: dict[str, Any] | None = None) -> None:
        """标记任务完成"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_status SET status='completed', result_json=?, updated_at=? WHERE task_id=?",
                (json.dumps(result or {}), self._now(), task_id),
            )
            conn.commit()

    def fail_task(self, task_id: str, error: str = "") -> None:
        """标记任务失败"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE task_status SET status='failed', result_json=?, updated_at=? WHERE task_id=?",
                (json.dumps({"error": error}), self._now(), task_id),
            )
            conn.commit()

    def get_interrupted_tasks(self) -> list[dict[str, Any]]:
        """获取所有中断任务（status=running 即为进程崩溃时未完成的任务）"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM task_status WHERE status='running' ORDER BY started_at",
            ).fetchall()
        tasks = [dict(row) for row in rows]
        if tasks:
            logger.warning("发现 %d 个中断任务待恢复", len(tasks))
        return tasks

    def recover_on_startup(self) -> list[dict[str, Any]]:
        """启动时自动检测中断任务

        将所有 status=running 的记录标记为 status=pending（待重新执行），
        并返回这些任务的列表供上层调度器重新入队。
        """
        interrupted = self.get_interrupted_tasks()
        if not interrupted:
            logger.info("无中断任务，跳过恢复")
            return []

        with self._connect() as conn:
            conn.execute(
                "UPDATE task_status SET status='pending', updated_at=? WHERE status='running'",
                (self._now(),),
            )
            conn.commit()

        for t in interrupted:
            logger.info(
                "任务已标记待恢复: id=%s session=%s type=%s",
                t["task_id"], t["session_id"], t["task_type"],
            )
        return interrupted

    def cleanup_completed(self, keep_days: int = 7) -> int:
        """清理已完成/失败的历史记录"""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM task_status WHERE status IN ('completed','failed') AND updated_at < ?",
                (cutoff,),
            )
            conn.commit()
            count = cursor.rowcount
        if count:
            logger.info("已清理 %d 条历史任务记录", count)
        return count
