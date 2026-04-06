"""
agents.intent_clarifier — 意图澄清反问节点
──────────────────────────────────────────────
职责：
  1. 当 analyze_node 检测到关键字段缺失时触发
  2. 基于 clarification_questions 生成专业的反问邮件
  3. 模拟买家补充回答（生产环境中由前端/小程序收集）
  4. 将补充信息合并回 raw_inquiry，递增 clarification_round
  5. 重新进入 analyze_node 形成闭环

工作流位置::

    analyze_node → [clarification_needed?]
                     ├─ True  → clarifier_node → analyze_node（重入）
                     └─ False → [is_valid_lead?] → ...
"""

from __future__ import annotations

from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from agents.state import TradeState
from core.logger import get_logger

logger = get_logger(__name__)

_MAX_CLARIFICATION_ROUNDS = 3

_CLARIFIER_SYSTEM_PROMPT: str = """\
You are a professional foreign trade sales assistant.

The customer sent an inquiry but some critical information is missing.
Your task: write a short, polite, professional follow-up email asking \
the customer to clarify the missing details.

Guidelines:
- Be warm and professional, not pushy.
- List the specific questions clearly (numbered).
- Keep it concise — no more than 8 sentences total.
- Sign off as "James Liu, Senior Sales Manager, TradeStealth Export Co., Ltd."

Output the email text only. No JSON, no code blocks."""


def clarifier_node(state: TradeState) -> dict[str, Any]:
    """LangGraph 节点 —— 生成澄清反问邮件

    当 ``clarification_needed == True`` 时由工作流条件路由调用。
    基于 ``clarification_questions`` 列表生成一封专业的反问邮件，
    并递增 ``clarification_round`` 计数器。

    在当前版本中，此节点生成反问邮件后将其存入
    ``generated_response``，同时将 ``clarification_needed``
    重置为 False 以终止本轮工作流。

    生产环境中，前端收到反问后应收集买家回答，
    将补充信息追加到 ``raw_inquiry`` 后重新触发工作流。

    Parameters
    ----------
    state : TradeState
        当前工作流状态

    Returns
    -------
    dict[str, Any]
        LangGraph 状态增量
    """
    questions: list[str] = state.get("clarification_questions", [])
    current_round: int = state.get("clarification_round", 0)
    intent: dict = state.get("analyzed_intent", {})

    if current_round >= _MAX_CLARIFICATION_ROUNDS:
        logger.warning("澄清轮次已达上限 (%d)，跳过反问", _MAX_CLARIFICATION_ROUNDS)
        return {
            "clarification_needed": False,
            "clarification_questions": [],
        }

    if not questions:
        logger.info("无待澄清问题，跳过反问节点")
        return {"clarification_needed": False}

    logger.info(
        "生成澄清反问邮件: round=%d questions=%d product=%s",
        current_round + 1,
        len(questions),
        intent.get("product", "N/A"),
    )

    llm = ChatOllama(model="qwen3:4b", temperature=0.5)

    questions_text = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
    product = intent.get("product", "your requested product")
    summary = intent.get("summary", "your inquiry")

    messages = [
        SystemMessage(content=_CLARIFIER_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Customer inquiry summary: {summary}\n"
                f"Product: {product}\n\n"
                f"Missing information — please ask these questions:\n{questions_text}"
            ),
        ),
    ]

    import re
    response = llm.invoke(messages)
    clarification_email = re.sub(
        r"<think>.*?</think>", "", response.content, flags=re.DOTALL,
    ).strip()

    logger.info(
        "澄清反问邮件已生成: round=%d length=%d",
        current_round + 1,
        len(clarification_email),
    )

    return {
        "generated_response": clarification_email,
        "clarification_needed": False,
        "clarification_round": current_round + 1,
    }
