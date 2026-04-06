"""
database.pg_checkpointer — LangGraph 检查点（PostgreSQL 连接池 + 内存降级）
──────────────────────────────────────────────────────────────────────
说明（与 LangGraph 2.x 对齐）：
  - ``AsyncPostgresSaver.from_conn_string`` 在新版中为 **async 上下文管理器**，
    不适合作为进程级单例持有；工业做法是用 **psycopg_pool.AsyncConnectionPool**
    + ``AsyncPostgresSaver(conn=pool)``，在应用生命周期内保持池开启。
  - 状态恢复使用官方 ``aget_tuple(config)``，读取 ``checkpoint["channel_values"]``。
  - SQLite / 缺依赖 / 建连失败 → ``MemorySaver``（单进程、非跨机恢复）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langchain_core.runnables import RunnableConfig

from core.logger import get_logger

logger = get_logger(__name__)

_checkpointer_instance: Any = None
_pg_pool: Any = None
# Postgres URL 下、尚未 await get_pg_checkpointer 时，构图仅用此 MemorySaver，不占用 _checkpointer_instance
_sync_compile_memory: MemorySaver | None = None
_checkpointer_lock = asyncio.Lock()


def _database_url() -> str:
    from database.models import DBSettings

    return DBSettings().DATABASE_URL


def _to_psycopg_conninfo(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def shutdown_pg_checkpointer() -> None:
    """应用停机时关闭连接池并清空单例（幂等）。"""
    global _checkpointer_instance, _pg_pool, _sync_compile_memory

    _checkpointer_instance = None
    _sync_compile_memory = None
    if _pg_pool is not None:
        try:
            await _pg_pool.close()
        except Exception as exc:
            logger.warning("PostgreSQL 连接池关闭异常: %s", exc)
        finally:
            _pg_pool = None


async def _build_postgres_checkpointer():
    global _pg_pool, _checkpointer_instance

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    conninfo = _to_psycopg_conninfo(_database_url())
    pool = AsyncConnectionPool(
        conninfo=conninfo,
        min_size=1,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    await pool.open()
    saver = AsyncPostgresSaver(conn=pool)
    await saver.setup()
    _pg_pool = pool
    _checkpointer_instance = saver
    logger.info("PostgreSQL Checkpointer 已就绪（连接池 + AsyncPostgresSaver）")
    return _checkpointer_instance


async def get_pg_checkpointer():
    """异步获取 checkpointer（推荐在 lifespan 中预热）。"""
    global _checkpointer_instance

    url = _database_url()

    async with _checkpointer_lock:
        if _checkpointer_instance is not None:
            return _checkpointer_instance

        if "sqlite" in url.lower():
            _checkpointer_instance = MemorySaver()
            logger.info("SQLite 环境 — 使用 MemorySaver 检查点")
            return _checkpointer_instance

        try:
            return await _build_postgres_checkpointer()
        except ImportError as exc:
            logger.warning("缺少依赖（psycopg_pool / checkpoint-postgres）: %s — MemorySaver", exc)
            _checkpointer_instance = MemorySaver()
            return _checkpointer_instance
        except Exception as exc:
            logger.warning("PostgreSQL Checkpointer 初始化失败: %s — MemorySaver", exc)
            _checkpointer_instance = MemorySaver()
            return _checkpointer_instance


def get_pg_checkpointer_sync():
    """同步获取（``graph.compile`` 时调用）。

    工业约定：在 FastAPI ``lifespan`` 内先 ``await get_pg_checkpointer()`` 建好连接池，
    再实例化 ``WorkflowOrchestrator``，此处即返回已缓存的 PG saver。
    Postgres 且尚未预热时：使用单独的 ``MemorySaver``，**不**写入 ``_checkpointer_instance``，
    避免阻塞后续异步连接池初始化。
    """
    global _checkpointer_instance, _sync_compile_memory

    if _checkpointer_instance is not None:
        return _checkpointer_instance

    url = _database_url()
    if "sqlite" in url.lower():
        _checkpointer_instance = MemorySaver()
        return _checkpointer_instance

    if _sync_compile_memory is None:
        logger.info(
            "Checkpointer 尚未异步预热 — 构图使用独立 MemorySaver；"
            "生产请在 lifespan 内先 await get_pg_checkpointer() 再构图",
        )
        _sync_compile_memory = MemorySaver()
    return _sync_compile_memory


async def recover_trade_state(thread_id: str) -> dict[str, Any] | None:
    """使用 ``aget_tuple`` 读取该 thread 最新 checkpoint 的 channel_values。"""
    checkpointer = await get_pg_checkpointer()

    if isinstance(checkpointer, MemorySaver):
        logger.debug("MemorySaver 模式，无法跨进程恢复 thread=%s", thread_id)
        return None

    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    try:
        tup = await checkpointer.aget_tuple(config)
        if tup is None:
            logger.info("未找到 thread=%s 的检查点", thread_id)
            return None
        chk = tup.checkpoint
        if not isinstance(chk, dict):
            return None
        vals = chk.get("channel_values")
        if not isinstance(vals, dict):
            return {}
        logger.info("已恢复 thread=%s 状态字段数=%d", thread_id, len(vals))
        return dict(vals)
    except Exception as exc:
        logger.error("aget_tuple 恢复失败 thread=%s: %s", thread_id, exc)
        return None


async def list_active_threads(limit: int = 100) -> list[dict[str, Any]]:
    """枚举最近检查点（依赖 ``alist``；过滤失败时返回空列表）。"""
    checkpointer = await get_pg_checkpointer()

    if isinstance(checkpointer, MemorySaver):
        return []

    out: list[dict[str, Any]] = []
    try:
        n = 0
        async for ct in checkpointer.alist(None, limit=limit):
            cfg = ct.config.get("configurable", {}) if isinstance(ct.config, dict) else {}
            out.append({
                "thread_id": cfg.get("thread_id", ""),
                "checkpoint_id": cfg.get("checkpoint_id", ""),
            })
            n += 1
            if n >= limit:
                break
    except Exception as exc:
        logger.warning("alist 枚举检查点失败: %s", exc)
        return []
    return out
