"""
models.a2a_protocol — A2A (Agent-to-Agent) 协议数据模型
═══════════════════════════════════════════════════════
2026 A2A 标准：结构化握手 + 强校验 + 追溯字段
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TurnStatus(str, Enum):
    """A2A 谈判轮次状态"""
    OFFER = "OFFER"
    COUNTER_OFFER = "COUNTER_OFFER"
    REJECT = "REJECT"


class AgentCard(BaseModel):
    """A2A 智能体身份卡"""
    agent_id: str = Field(..., min_length=1, description="Agent unique identifier")
    capabilities: list[str] = Field(default_factory=list, description="Agent capabilities")
    endpoint: str = Field(..., min_length=1, description="Agent communication endpoint")


class A2APayload(BaseModel):
    """A2A 协议标准载荷 — 多智能体通讯的原子数据单元

    所有跨智能体报价必须通过此模型强校验，
    缺失 sell_side_transaction_id 将触发 TransactionContextMissing。
    """
    agent_card: AgentCard
    negotiation_round: int = Field(ge=0, description="Current negotiation round")
    turn_status: TurnStatus = Field(default=TurnStatus.OFFER)
    proposed_price: Decimal = Field(ge=0, description="Proposed unit price USD")
    moq: int = Field(ge=1, description="Minimum order quantity")
    sell_side_transaction_id: str = Field(
        ..., min_length=1,
        description="Sell-side transaction ID for back-to-back tracing",
    )
    sku_name: str = Field(default="", description="SKU name")
    available_qty: int = Field(default=0, ge=0)
    profit_margin_pct: float = Field(default=0.0)

    @field_validator("sell_side_transaction_id")
    @classmethod
    def _validate_txn_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("sell_side_transaction_id must not be empty")
        return v.strip()
