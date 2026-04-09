"""
agents.c_intent_agent — 客户询盘意图解析节点（RFQ 结构化版）
──────────────────────────────────────────────────────────────
职责：
  1. 接收原始英文询盘邮件（含拼写错误、冗长描述）
  2. 调用本地 Ollama qwen3:4b 提取完整 RFQ 结构化字段
     （产品/数量/预算/MOQ偏好/贸易术语/认证/目标港口/付款方式等）
  3. 判定是否为有效商业线索（过滤垃圾邮件 / 钓鱼 / 无关内容）
  4. 检测关键字段缺失，标记是否需要澄清反问
  5. 以结构化 JSON 写入 TradeState.analyzed_intent
"""

from __future__ import annotations

from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from agents.state import TradeState, extract_json_from_llm
from core.logger import get_logger
from core.system_prompt import OMNIEDGE_CORE_COMPACT

logger = get_logger(__name__)


# ─── RFQ 结构化提取 System Prompt ────────────────────────────
_INTENT_SYSTEM_PROMPT: str = """\
You are a professional foreign trade RFQ (Request for Quotation) analyst.

Your task: analyze the incoming customer inquiry and output a JSON object \
with the full structured RFQ fields used in international B2B trade.

Extract the following fields:
- "product": the product(s) the customer is asking about (string)
- "quantity": estimated quantity or order size (integer, 0 if unknown)
- "budget_usd": estimated budget in USD (number, 0 if unknown)
- "destination_country": target country/region for delivery (string, "unknown" if not mentioned)
- "target_port": specific destination port if mentioned (string, "unknown" if not mentioned)
- "urgency": how urgent — one of "low", "medium", "high"
- "moq_preference": buyer's preferred minimum order quantity (integer, 0 if not mentioned)
- "trade_term": preferred trade term — one of "FOB", "CIF", "EXW", "DDP", "unknown"
- "certs_required": list of required certifications (e.g. ["CE", "FCC", "RoHS", "UL", "IEC", "TUV"])
- "voltage_spec": voltage specification if applicable (string, "unknown" if not mentioned)
- "payment_preference": preferred payment method — one of "T/T", "L/C", "D/P", "unknown"
- "sample_needed": whether buyer requests samples (boolean)
- "delivery_deadline": expected delivery date or timeframe (string, "unknown" if not mentioned)
- "key_requirements": list of any other special requirements mentioned
- "summary": one-sentence summary of the customer's core intent
- "is_valid_lead": boolean — true if genuine business inquiry; false if spam/phishing/irrelevant
- "missing_fields": list of critical field names that the buyer did NOT provide \
and should be clarified. Critical fields are: quantity, budget_usd, certs_required, \
trade_term, voltage_spec (for electronics). Only list fields that are truly missing \
and important for quoting.

Rules:
- Tolerate spelling errors and poor grammar; extract meaning regardless.
- If budget is in another currency, convert roughly to USD.
- If the email is clearly spam, marketing blast, or not a real business inquiry, \
set is_valid_lead to false and missing_fields to [].
- Output ONLY valid JSON. No explanations outside JSON."""

# 需要澄清的关键字段（缺失任一则触发反问）
_CRITICAL_FIELDS: set[str] = {
    "quantity", "certs_required", "trade_term",
}


# ═════════════════════════════════════════════════════════════
#  LangGraph 节点
# ═════════════════════════════════════════════════════════════
def analyze_node(state: TradeState) -> dict[str, Any]:
    """LangGraph 节点 —— 解析客户询盘邮件意图（RFQ 结构化版）

    调用本地 Ollama ``qwen3:4b`` 模型，从原始邮件中提取完整的
    外贸 RFQ 结构化字段，并判定是否为有效商业线索。
    同时检测关键字段缺失，标记是否需要澄清反问。

    Parameters
    ----------
    state : TradeState
        当前工作流状态，需包含 ``raw_inquiry`` 字段

    Returns
    -------
    dict[str, Any]
        LangGraph 状态增量，包含:
        - ``analyzed_intent``         : dict — 结构化 RFQ JSON
        - ``is_valid_lead``           : bool — 是否为有效线索
        - ``clarification_needed``    : bool — 是否需要反问澄清
        - ``clarification_questions`` : list — 待澄清问题列表
    """
    raw_inquiry: str = state["raw_inquiry"]
    current_round: int = state.get("clarification_round", 0)
    logger.info("开始意图解析, 邮件长度=%d 字符, 澄清轮次=%d", len(raw_inquiry), current_round)

    llm: ChatOllama = ChatOllama(
        model="qwen3:4b",
        temperature=0.1,
    )

    messages: list = [
        SystemMessage(content=OMNIEDGE_CORE_COMPACT + "\n\n" + _INTENT_SYSTEM_PROMPT),
        HumanMessage(content=f"Customer email:\n\n{raw_inquiry}"),
    ]

    response = llm.invoke(messages)
    parsed: dict[str, Any] = extract_json_from_llm(response.content)
    is_valid: bool = bool(parsed.get("is_valid_lead", False))

    # ── 关键字段缺失检测 → 是否需要澄清反问 ──
    missing: list[str] = parsed.get("missing_fields", [])
    critical_missing = [f for f in missing if f in _CRITICAL_FIELDS]
    needs_clarification = bool(critical_missing) and is_valid and current_round < 3

    questions: list[str] = []
    if needs_clarification:
        questions = _build_clarification_questions(critical_missing, parsed)

    logger.info(
        "意图解析完成: product=%s urgency=%s is_valid=%s missing=%s clarify=%s",
        parsed.get("product", "N/A"),
        parsed.get("urgency", "N/A"),
        is_valid,
        critical_missing,
        needs_clarification,
    )

    return {
        "analyzed_intent": parsed,
        "is_valid_lead": is_valid,
        "clarification_needed": needs_clarification,
        "clarification_questions": questions,
    }


def _build_clarification_questions(
    missing_fields: list[str],
    parsed_intent: dict[str, Any],
) -> list[str]:
    """根据缺失字段生成专业的外贸反问问题"""
    product = parsed_intent.get("product", "your product")
    questions: list[str] = []

    field_question_map: dict[str, str] = {
        "quantity": f"Could you confirm the exact quantity needed for {product}?",
        "budget_usd": f"What is your target budget (in USD) for this order of {product}?",
        "certs_required": (
            f"Do you require any specific certifications for {product}? "
            "For example: CE, FCC, RoHS, UL, IEC, TUV."
        ),
        "trade_term": (
            "What is your preferred trade term? "
            "FOB (you arrange shipping) or CIF (we arrange shipping to your port)?"
        ),
        "voltage_spec": (
            f"Could you specify the required voltage for {product}? "
            "For example: 110V, 220V, or a specific range."
        ),
    }

    for field in missing_fields:
        q = field_question_map.get(field)
        if q:
            questions.append(q)

    return questions
