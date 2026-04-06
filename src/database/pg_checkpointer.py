"""
database.pg_checkpointer — 分布式 PostgreSQL 状态持久化
──────────────────────────────────────────────────────
职责：
  1. 将 LangGraph 工作流状态从 MemorySaver 迁移至 PostgreSQL
  2. 基于 langgraph-checkpoint-postgres 的 AsyncPostgresSaver
  3. 实现 99.99% 高可用：Hub 崩溃后从数据库加载状态断点恢复
  4. 提供统一的 checkpointer 工厂函数，供所有工作流图使用
  5. 回退机制：PostgreSQL 不可用时降级为 MemorySaver

架构要点：
  - 所有 TradeState / MatchState 上下文持久化至 PG
  - 谈判状态机（报价博弈、询价阶段）可从断点自动恢复
  - thread_id 作为分区键，支持多租户并发
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from core.logger import get_logger

logger = get_logger(__name__)

# 全局单例缓存
_checkpointer_instance = None
_checkpointer_lock = asyncio.Lock()


async def get_pg_checkpointer():
    """获取 AsyncPostgresSaver 实例（单例 + 自动初始化）

    首次调用时：
      1. 从 DATABASE_URL 环境变量读取 PostgreSQL 连接串
      2. 创建 AsyncPostgresSaver 并调用 .setup() 初始化表结构
      3. 缓存实例供后续复用

    如果 PostgreSQL 不可用，降级为 MemorySaver 并记录警告。

    Returns
    -------
    AsyncPostgresSaver | MemorySaver
        LangGraph 兼容的 checkpointer 实例
    """
    global _checkpointer_instance

    if _checkpointer_instance is not None:
        return _checkpointer_instance

    async with _checkpointer_lock:
        # 双重检查
        if _checkpointer_instance is not None:
            return _checkpointer_instance

        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from database.models import DBSettings

            db_url = DBSettings().DATABASE_URL

            # langgraph-checkpoint-postgres 需要原生 psycopg 连接串
            # 将 asyncpg 格式转换为 psycopg 格式
            pg_conn_str = db_url.replace("postgresql+asyncpg://", "postgresql://")

            saver = AsyncPostgresSaver.from_conn_string(pg_conn_str)
            await saver.setup()

            _checkpointer_instance = saver
            logger.info(
                "PostgreSQL Checkpointer 已就绪 — 状态持久化已启用 (高可用模式)"
            )
            return _checkpointer_instance

        except ImportError:
            logger.warning(
                "langgraph-checkpoint-postgres 未安装，降级为 MemorySaver。"
                "安装命令: pip install langgraph-checkpoint-postgres"
            )
            _checkpointer_instance = MemorySaver()
            return _checkpointer_instance

        except Exception as exc:
            logger.warning(
                "PostgreSQL Checkpointer 初始化失败: %s — 降级为 MemorySaver", exc
            )
            _checkpointer_instance = MemorySaver()
            return _checkpointer_instance


def get_pg_checkpointer_sync():
    """同步版本：获取 checkpointer（用于非异步上下文的图构建）

    如果异步实例已缓存，直接返回。
    否则尝试同步初始化，失败则降级为 MemorySaver。

    Returns
    -------
    AsyncPostgresSaver | MemorySaver
    """
    global _checkpointer_instance

    if _checkpointer_instance is not None:
        return _checkpointer_instance

    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from database.models import DBSettings

        db_url = DBSettings().DATABASE_URL
        pg_conn_str = db_url.replace("postgresql+asyncpg://", "postgresql://")

        saver = AsyncPostgresSaver.from_conn_string(pg_conn_str)

        # setup() 需要异步调用，在同步上下文中通过 event loop 执行
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(saver.setup())
        finally:
            loop.close()

        _checkpointer_instance = saver
        logger.info("PostgreSQL Checkpointer (sync init) 已就绪")
        return _checkpointer_instance

    except ImportError:
        logger.warning("langgraph-checkpoint-postgres 未安装，降级为 MemorySaver")
        _checkpointer_instance = MemorySaver()
        return _checkpointer_instance

    except Exception as exc:
        logger.warning("PostgreSQL Checkpointer sync init 失败: %s — 降级", exc)
        _checkpointer_instance = MemorySaver()
        return _checkpointer_instance


async def recover_trade_state(thread_id: str) -> dict[str, Any] | None:
    """从 PostgreSQL 恢复指定 thread_id 的最新工作流状态

    用于 Hub 崩溃后的断点恢复：
      1. 查询 checkpointer 中该 thread_id 的最新 checkpoint
      2. 返回完整的 TradeState / MatchState 快照
      3. 调用方可基于此状态重新 invoke 工作流

    Parameters
    ----------
    thread_id : str
        工作流线程标识（通常为 session_id 或 negotiation_id）

    Returns
    -------
    dict | None
        最新状态快照，或 None（无历史记录）
    """
    checkpointer = await get_pg_checkpointer()

    if isinstance(checkpointer, MemorySaver):
        # MemorySaver 不支持跨进程恢复
        logger.debug("MemorySaver 模式，无法跨进程恢复 thread=%s", thread_id)
        return None

    try:
        config = {"configurable": {"thread_id": thread_id}}
        checkpoint = await checkpointer.aget(config)

        if checkpoint is None:
            logger.info("未找到 thread=%s 的历史状态", thread_id)
            return None

        # checkpoint 结构: {"channel_values": {...state...}, ...}
        state = checkpoint.get("channel_values", {})
        logger.info(
            "已恢复 thread=%s 的工作流状态 (keys=%d)",
            thread_id, len(state),
        )
        return dict(state)

    except Exception as exc:
        logger.error("状态恢复失败 thread=%s: %s", thread_id, exc)
        return None


async def list_active_threads(limit: int = 100) -> list[dict[str, Any]]:
    """列出所有活跃的工作流线程（用于崩溃恢复扫描）

    Returns
    -------
    list[dict]
        每个元素包含 thread_id 和最后更新时间
    """
    checkpointer = await get_pg_checkpointer()

    if isinstance(checkpointer, MemorySaver):
        return []

    try:
        threads = []
        async for checkpoint_tuple in checkpointer.alist(limit=limit):
            config = checkpoint_tuple.config
            tid = config.get("configurable", {}).get("thread_id", "")
            threads.append({
                "thread_id": tid,
                "checkpoint_id": checkpoint_tuple.checkpoint.get("id", ""),
            })
        return threads

    except Exception as exc:
        logger.error("列出活跃线程失败: %s", exc)
        return []
