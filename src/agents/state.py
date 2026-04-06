"""
agents.state — LangGraph 工作流共享状态定义
────────────────────────────────────────────
职责：
  1. 以 TypedDict 声明工作流中所有节点共享的状态结构
  2. 提供 extract_json_from_llm() 工具函数，
     兼容 qwen3 <think> 标签 / Markdown 代码块 / 裸 JSON
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict


# ═════════════════════════════════════════════════════════════
#  工作流状态
# ═════════════════════════════════════════════════════════════
class TradeState(TypedDict, total=False):
    """外贸邮件自动化处理工作流的全局状态

    ``total=False`` 允许节点以增量字典更新状态，
    LangGraph 会自动合并各节点返回的键值对。

    Attributes
    ----------
    raw_inquiry : str
        客户原始询盘邮件全文（含拼写错误、冗长描述等原始内容）
    analyzed_intent : dict[str, Any]
        意图解析节点输出的结构化 JSON —— 包含 product / budget /
        urgency / destination_country / key_requirements / summary /
        is_valid_lead 等字段
    generated_response : str
        策略回信节点生成的英文回信草稿（仅有效询盘才会生成）
    is_valid_lead : bool
        意图解析节点判定的布尔值：
        True = 有效商业询盘，False = 垃圾邮件或无效内容
    clarification_needed : bool
        意图解析后是否需要向买家反问澄清（缺少关键字段时为 True）
    clarification_questions : list[str]
        待澄清问题列表（如 "请确认电压规格" / "是否需要 CE 认证"）
    clarification_round : int
        当前澄清轮次（防止无限循环，上限 3 轮）
    """

    raw_inquiry: str
    analyzed_intent: dict[str, Any]
    generated_response: str
    is_valid_lead: bool
    clarification_needed: bool
    clarification_questions: list[str]
    clarification_round: int


# ═════════════════════════════════════════════════════════════
#  LLM 输出解析工具
# ═════════════════════════════════════════════════════════════
def extract_json_from_llm(text: str) -> dict[str, Any]:
    """从 LLM 原始输出中鲁棒地提取 JSON 对象

    依次尝试三种策略：
      1. 剥离 ``<think>…</think>`` 标签后，在 Markdown 代码块内匹配
      2. 在剩余文本中匹配最外层 ``{…}``
      3. 将整段文本直接作为 JSON 解析

    Parameters
    ----------
    text : str
        LLM 返回的原始字符串（可能含思考链 / 代码块 / 多余文字）

    Returns
    -------
    dict[str, Any]
        解析后的 JSON 字典

    Raises
    ------
    ValueError
        三种策略均失败时抛出，附带截断的原始文本便于调试
    """
    cleaned: str = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    md_match: re.Match[str] | None = re.search(
        r"```(?:json)?\s*\n?(.*?)```", cleaned, re.DOTALL,
    )
    if md_match:
        try:
            return json.loads(md_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    json_match: re.Match[str] | None = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        raise ValueError(f"无法从 LLM 输出中提取 JSON: {cleaned[:300]}")
