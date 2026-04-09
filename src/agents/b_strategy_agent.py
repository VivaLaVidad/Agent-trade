"""
agents.b_strategy_agent — 外贸策略回信生成节点
──────────────────────────────────────────────
职责：
  1. 消费 analyzed_intent 中的结构化意图
  2. 调用本地 Ollama qwen3:4b，注入高级外贸业务员 System Prompt
  3. 生成一封得体、专业、促单但不卑不亢的英文回信草稿
  4. 写入 TradeState.generated_response
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from agents.state import TradeState
from core.logger import get_logger
from core.system_prompt import OMNIEDGE_CORE_COMPACT

logger = get_logger(__name__)


# ─── System Prompt ───────────────────────────────────────────
_STRATEGY_SYSTEM_PROMPT: str = """\
You are a senior foreign trade sales specialist with 15+ years of experience \
at a leading manufacturing & export company.

Your task: based on the parsed customer intent (provided as JSON), \
write a professional English reply email.

Tone & style guidelines:
- Professional, warm, and confident — never servile or desperate.
- Demonstrate deep product expertise and industry credibility.
- Address EVERY concern the customer raised (product specs, certifications, \
pricing terms, delivery timeline, samples, etc.).
- Provide concrete next steps: quotation timeline, sample arrangement, \
technical datasheets, etc.
- Create subtle urgency (limited stock, seasonal pricing) without being pushy.
- Use a proper business email structure: greeting → body → call-to-action → sign-off.
- Sign off as "James Liu, Senior Sales Manager, OmniEdge (全域工联) Industrial Trade Network"

Output the complete email (Subject line + Body). Do NOT wrap in JSON or code blocks."""


# ═════════════════════════════════════════════════════════════
#  辅助函数
# ═════════════════════════════════════════════════════════════
def _strip_think_tags(text: str) -> str:
    """移除 qwen3 系列模型可能输出的 <think>…</think> 思考链标签

    Parameters
    ----------
    text : str
        LLM 原始输出

    Returns
    -------
    str
        清理后的纯内容文本
    """
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ═════════════════════════════════════════════════════════════
#  LangGraph 节点
# ═════════════════════════════════════════════════════════════
def draft_node(state: TradeState) -> dict[str, Any]:
    """LangGraph 节点 —— 生成外贸专业英文回信草稿

    仅在 ``is_valid_lead == True`` 时由工作流条件路由调用。
    注入高级外贸业务员人格的 System Prompt，基于解析后的客户意图
    生成一封促单、不卑不亢的英文回信。

    Parameters
    ----------
    state : TradeState
        当前工作流状态，需包含 ``analyzed_intent`` 字段

    Returns
    -------
    dict[str, Any]
        LangGraph 状态增量，包含:
        - ``generated_response`` : str — 完整的英文回信草稿
    """
    intent: dict[str, Any] = state["analyzed_intent"]
    logger.info("开始生成回信草稿, product=%s", intent.get("product", "N/A"))

    llm: ChatOllama = ChatOllama(
        model="qwen3:4b",
        temperature=0.7,
    )

    intent_json: str = json.dumps(intent, ensure_ascii=False, indent=2)
    messages: list = [
        SystemMessage(content=OMNIEDGE_CORE_COMPACT + "\n\n" + _STRATEGY_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Parsed customer intent:\n"
                f"```json\n{intent_json}\n```\n\n"
                f"Please draft the reply email now."
            ),
        ),
    ]

    response = llm.invoke(messages)
    reply_text: str = _strip_think_tags(response.content)

    logger.info("回信草稿生成完成, 长度=%d 字符", len(reply_text))

    return {"generated_response": reply_text}
