"""
modules.supply_chain.ledger — 交易账本 + 数字签名 (Ticker 绑定)
═══════════════════════════════════════════════════════════════
职责：
  1. 撮合成交后自动生成交易流水（TransactionLedger 记录）
  2. 路由费计算：成交金额 * fee_rate（默认 1%）
  3. SHA256 数字签名：对流水核心字段做防篡改哈希（含 Ticker ID）
  4. 提供账本查询接口
  5. **Ticker 绑定**: 所有流水必须关联标准化 Ticker ID
  6. **OperationalError 重试**: 所有 PG 操作包裹死锁重试；用尽后向上抛出，避免静默丢账
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_FEE_RATE: float = 0.01
_MAX_DB_RETRIES: int = 3


class LedgerService:
    """交易账本服务 —— 撮合成交即自动记账 + 签名 (Ticker 绑定)

    每笔流水包含:
    - transaction_id: UUID 唯一流水号
    - ticker_id: 标准化资产 Ticker ID
    - merchant_id / client_id: 多租户标识
    - amount_usd: 成交金额
    - routing_fee_usd: 平台路由费（默认 1%）
    - signature: SHA256(transaction_id|ticker_id|merchant_id|amount|fee|timestamp)
    """

    def __init__(self, fee_rate: float = _DEFAULT_FEE_RATE) -> None:
        self._fee_rate = fee_rate

    @staticmethod
    def _sign(payload: str) -> str:
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def create_transaction(
        self,
        merchant_id: str,
        client_id: str,
        amount_usd: float,
        match_id: str = "",
        po_number: str = "",
        ticker_id: str = "",
    ) -> dict[str, Any]:
        """生成一条交易流水并签名

        Parameters
        ----------
        merchant_id : str
            撮合商户ID
        client_id : str
            C端客户ID
        amount_usd : float
            成交金额(USD)
        match_id : str
            关联的撮合记录ID
        po_number : str
            关联的采购订单编号
        ticker_id : str
            标准化 Ticker ID (如 CLAW-ELEC-CAP-100NF50V)

        Returns
        -------
        dict
            完整的流水记录（含签名），可直接写入数据库
        """
        txn_id = str(uuid.uuid4())
        fee = round(amount_usd * self._fee_rate, 2)
        ts = datetime.now(timezone.utc).isoformat()

        # 签名包含 ticker_id
        sign_payload = f"{txn_id}|{ticker_id}|{merchant_id}|{amount_usd}|{fee}|{ts}"
        signature = self._sign(sign_payload)

        record = {
            "transaction_id": txn_id,
            "ticker_id": ticker_id,
            "merchant_id": merchant_id,
            "client_id": client_id,
            "match_id": match_id,
            "po_number": po_number,
            "amount_usd": amount_usd,
            "routing_fee_usd": fee,
            "fee_rate": self._fee_rate,
            "signature": signature,
            "status": "settled",
            "created_at": ts,
        }

        logger.info(
            "账本流水: txn=%s ticker=%s merchant=%s amount=$%.2f fee=$%.2f sig=%s…",
            txn_id[:8], ticker_id or "N/A", merchant_id[:8],
            amount_usd, fee, signature[:12],
        )
        return record

    def verify_signature(self, record: dict[str, Any]) -> bool:
        """验证流水签名是否被篡改"""
        sign_payload = (
            f"{record['transaction_id']}|{record.get('ticker_id', '')}|{record['merchant_id']}|"
            f"{record['amount_usd']}|{record['routing_fee_usd']}|{record['created_at']}"
        )
        expected = self._sign(sign_payload)
        return expected == record.get("signature", "")

    async def persist(self, record: dict[str, Any]) -> None:
        """将流水写入数据库 (含 OperationalError 重试)"""
        from sqlalchemy.exc import OperationalError

        last_oe: OperationalError | None = None
        for attempt in range(1, _MAX_DB_RETRIES + 1):
            try:
                from database.models import AsyncSessionFactory
                from modules.supply_chain.models import TransactionLedger

                async with AsyncSessionFactory() as session:
                    entry = TransactionLedger(
                        transaction_id=record["transaction_id"],
                        ticker_id=record.get("ticker_id", ""),
                        merchant_id=record["merchant_id"],
                        client_id=record.get("client_id", ""),
                        match_id=record.get("match_id") or None,
                        po_number=record.get("po_number", ""),
                        amount_usd=record["amount_usd"],
                        routing_fee_usd=record["routing_fee_usd"],
                        fee_rate=record["fee_rate"],
                        signature=record["signature"],
                        status=record.get("status", "settled"),
                    )
                    session.add(entry)
                    await session.commit()
                return  # 成功
            except OperationalError as exc:
                last_oe = exc
                logger.warning(
                    "账本入库 OperationalError (attempt %d/%d): %s",
                    attempt, _MAX_DB_RETRIES, exc,
                )
                if attempt < _MAX_DB_RETRIES:
                    await asyncio.sleep(0.05 * attempt)
                else:
                    logger.error("账本入库最终失败 (死锁/连接): %s", exc)
            except Exception as exc:
                logger.error("账本入库失败: %s", exc)
                raise
        if last_oe is not None:
            raise last_oe

    async def query_by_merchant(self, merchant_id: str, limit: int = 50) -> list[dict]:
        """查询指定商户的交易流水 (含 OperationalError 重试)"""
        from sqlalchemy.exc import OperationalError

        last_oe: OperationalError | None = None
        for attempt in range(1, _MAX_DB_RETRIES + 1):
            try:
                from database.models import AsyncSessionFactory
                from modules.supply_chain.models import TransactionLedger
                from sqlalchemy import select

                async with AsyncSessionFactory() as session:
                    stmt = (
                        select(TransactionLedger)
                        .where(TransactionLedger.merchant_id == merchant_id)
                        .order_by(TransactionLedger.created_at.desc())
                        .limit(limit)
                    )
                    rows = (await session.execute(stmt)).scalars().all()
                    return [
                        {
                            "transaction_id": r.transaction_id,
                            "ticker_id": getattr(r, "ticker_id", ""),
                            "amount_usd": r.amount_usd,
                            "routing_fee_usd": r.routing_fee_usd,
                            "po_number": r.po_number,
                            "status": r.status,
                            "signature": r.signature[:16] + "…",
                            "created_at": str(r.created_at),
                        }
                        for r in rows
                    ]
            except OperationalError as exc:
                last_oe = exc
                logger.warning(
                    "账本查询 OperationalError (attempt %d/%d): %s",
                    attempt, _MAX_DB_RETRIES, exc,
                )
                if attempt < _MAX_DB_RETRIES:
                    await asyncio.sleep(0.05 * attempt)
                else:
                    logger.error("账本查询最终失败: %s", exc)
            except Exception as exc:
                logger.error("账本查询失败: %s", exc)
                raise
        if last_oe is not None:
            raise last_oe
        return []
