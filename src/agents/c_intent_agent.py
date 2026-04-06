"""
agents.c_intent_agent — 客户询盘意图解析节点
──────────────────────────────────────────────
职责：
  1. 接收原始英文询盘邮件（含拼写错误、冗长描述）
  2. 调用本地 Ollama qwen3:4b 提取 意向产品 / 预算 / 紧迫度
  3. 判定是否为有效商业线索（过滤垃圾邮件 / 钓鱼 / 无关内容）
  4. 以结构化 JSON 写入 TradeState.analyzed_intent
"""

from __future__ import annotations

from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from agents.state import TradeState, extract_json_from_llm
from core.logger import get_logger

logger = get_logger(__name__)


# ─── System Prompt ───────────────────────────────────────────
_INTENT_SYSTEM_PROMPT: str = """\
You are a professional foreign trade email analyst.

Your task: analyze the incoming customer inquiry email and output a JSON object.

Extract the following fields:
- "product": the product(s) the customer is asking about (string)
- "quantity": estimated quantity or order size (string, "unknown" if not mentioned)
- "budget": estimated budget or price expectation (string, "unknown" if not mentioned)
- "destination_country": target country/region for delivery (string, "unknown" if not mentioned)
- "urgency": how urgent the inquiry is — one of "low", "medium", "high"
- "key_requirements": a list of special requirements mentioned \
(certifications, shipping terms, samples, etc.)
- "summary": one-sentence summary of the customer's core intent
- "is_valid_lead": boolean — true if this is a genuine business inquiry; \
false if it is spam, phishing, or irrelevant

Rules:
- Tolerate spelling errors and poor grammar; extract meaning regardless.
- If the email is clearly spam, marketing blast, or not a real business inquiry, \
set is_valid_lead to false.
- Output ONLY valid JSON. No explanations outside JSON."""


# ═════════════════════════════════════════════════════════════
#  LangGraph 节点
# ═════════════════════════════════════════════════════════════
def analyze_node(state: TradeState) -> dict[str, Any]:
    """LangGraph 节点 —— 解析客户询盘邮件意图

    调用本地 Ollama ``qwen3:4b`` 模型，从原始邮件中提取结构化
    意图信息，并判定是否为有效商业线索。

    Parameters
    ----------
    state : TradeState
        当前工作流状态，需包含 ``raw_inquiry`` 字段

    Returns
    -------
    dict[str, Any]
        LangGraph 状态增量，包含:
        - ``analyzed_intent`` : dict — 结构化意图 JSON
        - ``is_valid_lead``   : bool — 是否为有效线索
    """
    raw_inquiry: str = state["raw_inquiry"]
    logger.info("开始意图解析, 邮件长度=%d 字符", len(raw_inquiry))

    llm: ChatOllama = ChatOllama(
        model="qwen3:4b",
        temperature=0.1,
    )

    messages: list = [
        SystemMessage(content=_INTENT_SYSTEM_PROMPT),
        HumanMessage(content=f"Customer email:\n\n{raw_inquiry}"),
    ]

    response = llm.invoke(messages)
    parsed: dict[str, Any] = extract_json_from_llm(response.content)
    is_valid: bool = bool(parsed.get("is_valid_lead", False))

    logger.info(
        "意图解析完成: product=%s urgency=%s is_valid=%s",
        parsed.get("product", "N/A"),
        parsed.get("urgency", "N/A"),
        is_valid,
    )

    return {
        "analyzed_intent": parsed,
        "is_valid_lead": is_valid,
    }
