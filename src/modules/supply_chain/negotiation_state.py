"""
modules.supply_chain.negotiation_state — 谈判状态机
───────────────────────────────────────────────────
职责：
  1. 管理多轮谈判的状态流转：Pending → Counter-offer → Accepted / Rejected
  2. 记录每轮谈判的买卖双方报价、差值高亮
  3. 持久化谈判轮次到 NegotiationRound 表
  4. 提供谈判历史查询接口

状态流转::

    pending ──→ counter_offer ──→ accepted
       │              │               │
       │              ↓               ↓
       └──────→ rejected ←────── expired
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from core.logger import get_logger

logger = get_logger(__name__)

NegotiationStatus = Literal["pending", "counter_offer", "accepted", "rejected", "expired"]

_VALID_TRANSITIONS: dict[NegotiationStatus, set[NegotiationStatus]] = {
    "pending":       {"counter_offer", "accepted", "rejected"},
    "counter_offer": {"counter_offer", "accepted", "rejected", "expired"},
    "accepted":      set(),   # 终态
    "rejected":      set(),   # 终态
    "expired":       set(),   # 终态
}

_MAX_ROUNDS = 10


class NegotiationStateMachine:
    """多轮谈判状态机

    管理单个撮合结果的谈判生命周期，支持多轮报价/还价，
    每轮记录买卖双方报价和差值高亮。
    """

    def __init__(
        self,
        match_id: str,
        demand_id: str = "",
        merchant_id: str = "",
        client_id: str = "",
    ) -> None:
        self.negotiation_id: str = str(uuid.uuid4())
        self.match_id: str = match_id
        self.demand_id: str = demand_id
        self.merchant_id: str = merchant_id
        self.client_id: str = client_id
        self.status: NegotiationStatus = "pending"
        self.current_round: int = 0
        self.rounds: list[dict[str, Any]] = []

    def submit_seller_offer(self, offer: dict[str, Any]) -> dict[str, Any]:
        """卖家提交报价（初始报价或还价）

        Parameters
        ----------
        offer : dict
            卖家报价，需包含 unit_price_usd, quantity, shipping_term 等

        Returns
        -------
        dict
            本轮谈判记录
        """
        self._check_can_advance()
        self.current_round += 1

        round_record = self._create_round(
            round_number=self.current_round,
            seller_offer=offer,
            buyer_offer=None,
            action="seller_quote",
        )
        self.rounds.append(round_record)

        if self.status == "pending":
            pass  # 保持 pending，等待买家响应
        logger.info(
            "卖家报价: negotiation=%s round=%d price=$%.4f",
            self.negotiation_id[:8], self.current_round,
            offer.get("unit_price_usd", 0),
        )
        return round_record

    def submit_buyer_response(
        self,
        action: Literal["accept", "counter", "reject"],
        counter_offer: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """买家响应（接受/还价/拒绝）

        Parameters
        ----------
        action : str
            "accept" / "counter" / "reject"
        counter_offer : dict | None
            还价时的买家报价

        Returns
        -------
        dict
            本轮谈判记录（含状态变更和差值高亮）
        """
        self._check_can_advance()

        if action == "accept":
            self._transition("accepted")
            round_record = self._create_round(
                round_number=self.current_round,
                seller_offer=self._last_seller_offer(),
                buyer_offer=counter_offer,
                action="buyer_accept",
            )
        elif action == "counter":
            if not counter_offer:
                raise ValueError("还价时必须提供 counter_offer")
            self._transition("counter_offer")
            round_record = self._create_round(
                round_number=self.current_round,
                seller_offer=self._last_seller_offer(),
                buyer_offer=counter_offer,
                action="buyer_counter",
            )
        elif action == "reject":
            self._transition("rejected")
            round_record = self._create_round(
                round_number=self.current_round,
                seller_offer=self._last_seller_offer(),
                buyer_offer=counter_offer,
                action="buyer_reject",
            )
        else:
            raise ValueError(f"未知操作: {action}")

        self.rounds.append(round_record)
        logger.info(
            "买家响应: negotiation=%s action=%s status=%s round=%d",
            self.negotiation_id[:8], action, self.status, self.current_round,
        )
        return round_record

    def get_delta_highlight(
        self,
        old_price: float,
        new_price: float,
    ) -> dict[str, Any]:
        """计算报价差值高亮

        Parameters
        ----------
        old_price : float
            上一轮报价
        new_price : float
            本轮报价

        Returns
        -------
        dict
            差值信息，含绝对值、百分比、方向
        """
        if old_price <= 0:
            return {"delta_usd": 0, "delta_pct": 0, "direction": "unchanged"}

        delta = round(new_price - old_price, 4)
        pct = round(delta / old_price * 100, 2)
        direction = "down" if delta < 0 else "up" if delta > 0 else "unchanged"

        return {
            "old_price_usd": old_price,
            "new_price_usd": new_price,
            "delta_usd": delta,
            "delta_pct": pct,
            "direction": direction,
        }

    def to_summary(self) -> dict[str, Any]:
        """导出谈判摘要"""
        return {
            "negotiation_id": self.negotiation_id,
            "match_id": self.match_id,
            "status": self.status,
            "total_rounds": self.current_round,
            "rounds": self.rounds,
            "final_offer": self._last_seller_offer(),
        }

    # ── 内部方法 ──────────────────────────────────────────────

    def _transition(self, new_status: NegotiationStatus) -> None:
        valid = _VALID_TRANSITIONS.get(self.status, set())
        if new_status not in valid:
            raise ValueError(
                f"非法状态转换: {self.status} → {new_status} "
                f"(允许: {valid})"
            )
        old = self.status
        self.status = new_status
        logger.debug("状态转换: %s → %s", old, new_status)

    def _check_can_advance(self) -> None:
        if self.status in ("accepted", "rejected", "expired"):
            raise ValueError(f"谈判已终结 (status={self.status})，无法继续")
        if self.current_round >= _MAX_ROUNDS:
            self._transition("expired")
            raise ValueError(f"谈判轮次已达上限 ({_MAX_ROUNDS})")

    def _last_seller_offer(self) -> dict[str, Any]:
        for r in reversed(self.rounds):
            if r.get("seller_offer"):
                return r["seller_offer"]
        return {}

    def _create_round(
        self,
        round_number: int,
        seller_offer: dict | None,
        buyer_offer: dict | None,
        action: str,
    ) -> dict[str, Any]:
        # 计算差值高亮
        delta = {}
        if seller_offer and buyer_offer:
            delta = self.get_delta_highlight(
                seller_offer.get("unit_price_usd", 0),
                buyer_offer.get("unit_price_usd", 0),
            )

        return {
            "round_id": str(uuid.uuid4()),
            "negotiation_id": self.negotiation_id,
            "round_number": round_number,
            "status": self.status,
            "action": action,
            "seller_offer": seller_offer,
            "buyer_offer": buyer_offer,
            "delta_highlight": delta,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    # ── 持久化 (含 OperationalError 重试) ─────────────────────

    async def persist_round(self, round_record: dict[str, Any]) -> None:
        """将谈判轮次写入数据库 (含死锁重试；用尽后抛出 OperationalError)"""
        from sqlalchemy.exc import OperationalError

        max_retries = 3
        last_oe: OperationalError | None = None
        for attempt in range(1, max_retries + 1):
            try:
                from database.models import AsyncSessionFactory
                from modules.supply_chain.models import NegotiationRound

                async with AsyncSessionFactory() as session:
                    entry = NegotiationRound(
                        id=round_record["round_id"],
                        negotiation_id=self.negotiation_id,
                        match_id=self.match_id,
                        demand_id=self.demand_id,
                        merchant_id=self.merchant_id,
                        client_id=self.client_id,
                        round_number=round_record["round_number"],
                        status=round_record["status"],
                        action=round_record["action"],
                        seller_offer=round_record.get("seller_offer"),
                        buyer_offer=round_record.get("buyer_offer"),
                        delta_highlight=round_record.get("delta_highlight"),
                    )
                    session.add(entry)
                    await session.commit()
                return  # 成功
            except OperationalError as exc:
                last_oe = exc
                logger.warning(
                    "谈判轮次入库 OperationalError (attempt %d/%d): %s",
                    attempt, max_retries, exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(0.05 * attempt)
                else:
                    logger.error("谈判轮次入库最终失败 (死锁/连接): %s", exc)
            except Exception as exc:
                logger.error("谈判轮次入库失败: %s", exc)
                raise
        if last_oe is not None:
            raise last_oe
