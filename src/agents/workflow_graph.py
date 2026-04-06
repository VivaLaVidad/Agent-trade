"""
agents.workflow_graph — LangGraph 外贸邮件处理工作流编排
─────────────────────────────────────────────────────────
职责：
  1. 使用 StateGraph 串联 意图解析 → 澄清反问 → 条件路由 → 回信生成 完整链路
  2. 三路条件路由：
     - 需要澄清 → clarifier_node → END（等待买家补充后重入）
     - 有效询盘 → draft_node → END
     - 垃圾邮件 → 直达 END
  3. Checkpointer：优先 PostgreSQL（langgraph-checkpoint-postgres），失败则 MemorySaver
  4. 对外提供 WorkflowOrchestrator 统一调用入口（兼容 main.py）

流程示意::

    START → analyze_node → [clarification_needed?]
                              ├─ True  → clarifier_node → END（等待买家补充）
                              └─ False → [is_valid_lead?]
                                           ├─ True  → draft_node → END
                                           └─ False ──────────────→ END
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents.b_strategy_agent import draft_node
from agents.c_intent_agent import analyze_node
from agents.intent_clarifier import clarifier_node
from agents.state import TradeState
from database.pg_checkpointer import get_pg_checkpointer_sync
from core.logger import get_logger

logger = get_logger(__name__)


# ═════════════════════════════════════════════════════════════
#  条件路由
# ═════════════════════════════════════════════════════════════
def _route_after_analysis(state: TradeState) -> str:
    """条件路由函数 —— 意图解析完成后决定下一步走向

    Parameters
    ----------
    state : TradeState
        当前工作流状态

    Returns
    -------
    str
        ``"needs_clarification"`` — 关键字段缺失，进入澄清反问节点
        ``"valid_lead"``         — 有效询盘，进入回信生成节点
        ``"junk_mail"``          — 垃圾邮件 / 无效线索，直接结束
    """
    if state.get("clarification_needed", False):
        logger.info("[路由] 关键字段缺失 → 进入澄清反问节点")
        return "needs_clarification"
    if state.get("is_valid_lead", False):
        logger.info("[路由] 有效询盘 → 进入回信生成节点")
        return "valid_lead"
    logger.info("[路由] 无效线索 / 垃圾邮件 → 流程结束")
    return "junk_mail"


# ═════════════════════════════════════════════════════════════
#  图构建
# ═════════════════════════════════════════════════════════════
def build_trade_graph():
    """构建外贸邮件处理 StateGraph 并编译（附带 PG / Memory 检查点）

    流程：
      analyze_node → 三路路由 → clarifier_node / draft_node / END

    Returns
    -------
    CompiledStateGraph
        编译后的 LangGraph 图实例，可直接调用 ``.invoke()`` / ``.ainvoke()``
    """
    graph: StateGraph = StateGraph(TradeState)

    graph.add_node("analyze_node", analyze_node)
    graph.add_node("clarifier_node", clarifier_node)
    graph.add_node("draft_node", draft_node)

    graph.set_entry_point("analyze_node")

    graph.add_conditional_edges(
        "analyze_node",
        _route_after_analysis,
        {
            "needs_clarification": "clarifier_node",
            "valid_lead": "draft_node",
            "junk_mail": END,
        },
    )

    # 澄清反问后结束本轮（等待买家补充信息后重新触发工作流）
    graph.add_edge("clarifier_node", END)
    graph.add_edge("draft_node", END)

    checkpointer = get_pg_checkpointer_sync()
    return graph.compile(checkpointer=checkpointer)


# ═════════════════════════════════════════════════════════════
#  Orchestrator（对外统一接口，兼容 main.py）
# ═════════════════════════════════════════════════════════════
class WorkflowOrchestrator:
    """工作流编排器 —— main.py 的唯一调用入口

    内部持有编译后的 LangGraph 图实例与可持久化 checkpointer（PG 或内存降级），
    以 ``session_id`` 作为 ``thread_id`` 区分不同客户的邮件上下文。
    """

    def __init__(self) -> None:
        self._graph = build_trade_graph()

    async def run(
        self,
        session_id: str,
        intent_text: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """执行完整的外贸邮件处理工作流

        Parameters
        ----------
        session_id : str
            会话唯一标识，同时作为 MemorySaver 的 ``thread_id``
        intent_text : str
            客户原始询盘邮件文本
        context : dict | None
            附加上下文（本阶段预留扩展）

        Returns
        -------
        dict[str, Any]
            工作流最终状态快照：analyzed_intent、generated_response、is_valid_lead、
            clarification_*（澄清反问相关）
        """
        initial_state: TradeState = {"raw_inquiry": intent_text}
        config: dict = {"configurable": {"thread_id": session_id}}

        final_state: dict = await self._graph.ainvoke(
            initial_state, config=config,
        )

        return {
            "analyzed_intent": final_state.get("analyzed_intent"),
            "generated_response": final_state.get("generated_response", ""),
            "is_valid_lead": final_state.get("is_valid_lead", False),
            "clarification_needed": final_state.get("clarification_needed", False),
            "clarification_questions": final_state.get("clarification_questions", []),
            "clarification_round": final_state.get("clarification_round", 0),
        }
