"""
core.long_term_memory — EpisodicMemory 长效交锋图谱
═══════════════════════════════════════════════════════
职责：
  1. 维护 opponent_profiles 表 — 记录每个客户的谈判行为画像
  2. 在交易达成/失败时异步更新画像（还价次数、成交折让率、风险标签）
  3. 在下次询盘时为 NegotiatorAgent 提供高优 Context 注入
  4. 动态调整初始报价策略（高频压价客户 +5%，忠实客户 -2%）

数据模型::

    opponent_profiles:
      client_id (PK), total_negotiations, total_accepted, total_rejected,
      avg_discount_pct, avg_counter_rounds, last_interaction,
      risk_tag (high_pressure | normal | premium)

暗箱原则：
  - 所有画像数据本地存储，零公网依赖
  - 画像更新为 fire-and-forget 异步操作，不阻塞主流程
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Float, Integer, String, DateTime, func, select
from sqlalchemy.orm import Mapped, mapped_column

from core.logger import get_logger

logger = get_logger(__name__)

_MAX_DB_RETRIES = 3


# ═══════════════════════════════════════════════════════════════
#  ORM Model — opponent_profiles
# ═══════════════════════════════════════════════════════════════

def _get_base():
    """延迟导入 Base 避免循环依赖"""
    from database.models import Base
    return Base


class OpponentProfile(_get_base()):
    """客户谈判行为画像 — 长效交锋图谱"""
    __tablename__ = "opponent_profiles"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    total_negotiations: Mapped[int] = mapped_column(Integer, default=0, comment="总谈判次数")
    total_accepted: Mapped[int] = mapped_column(Integer, default=0, comment="成交次数")
    total_rejected: Mapped[int] = mapped_column(Integer, default=0, comment="拒绝/失败次数")
    avg_discount_pct: Mapped[float] = mapped_column(Float, default=0.0, comment="平均成交折让率(%)")
    avg_counter_rounds: Mapped[float] = mapped_column(Float, default=0.0, comment="平均还价轮次")
    max_counter_rounds: Mapped[int] = mapped_column(Integer, default=0, comment="最大还价轮次")
    total_volume_usd: Mapped[float] = mapped_column(Float, default=0.0, comment="累计成交额(USD)")
    risk_tag: Mapped[str] = mapped_column(
        String(20), default="normal",
        comment="行为标签: high_pressure | normal | premium",
    )
    last_interaction: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="最近交互时间",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# ═══════════════════════════════════════════════════════════════
#  OpponentProfiler — 画像管理器
# ═══════════════════════════════════════════════════════════════

class OpponentProfiler:
    """客户谈判行为画像管理器

    提供画像的读取、更新和策略计算功能。
    所有 DB 操作包含 OperationalError 重试。
    """

    # 策略参数
    HIGH_PRESSURE_THRESHOLD = 3.0   # 平均还价轮次 > 3 → 高压客户
    PREMIUM_THRESHOLD = 5           # 成交次数 > 5 且折让率 < 5% → 优质客户
    HIGH_PRESSURE_MARKUP = 0.05     # 高压客户初始报价上浮 5%
    PREMIUM_DISCOUNT = -0.02        # 优质客户忠诚折扣 2%

    async def get_profile(self, client_id: str) -> dict[str, Any] | None:
        """获取客户画像

        Parameters
        ----------
        client_id : str
            客户唯一标识

        Returns
        -------
        dict | None
            画像数据，不存在则返回 None
        """
        from sqlalchemy.exc import OperationalError

        for attempt in range(1, _MAX_DB_RETRIES + 1):
            try:
                from database.models import AsyncSessionFactory

                async with AsyncSessionFactory() as session:
                    result = await session.get(OpponentProfile, client_id)
                    if result is None:
                        return None
                    return {
                        "client_id": result.client_id,
                        "total_negotiations": result.total_negotiations,
                        "total_accepted": result.total_accepted,
                        "total_rejected": result.total_rejected,
                        "avg_discount_pct": result.avg_discount_pct,
                        "avg_counter_rounds": result.avg_counter_rounds,
                        "max_counter_rounds": result.max_counter_rounds,
                        "total_volume_usd": result.total_volume_usd,
                        "risk_tag": result.risk_tag,
                        "last_interaction": str(result.last_interaction) if result.last_interaction else None,
                    }
            except OperationalError as exc:
                logger.warning("画像查询 OperationalError (attempt %d/%d): %s", attempt, _MAX_DB_RETRIES, exc)
                if attempt >= _MAX_DB_RETRIES:
                    return None
            except Exception as exc:
                logger.debug("画像查询跳过: %s", exc)
                return None
        return None

    async def update_profile(
        self,
        client_id: str,
        outcome: str,
        discount_pct: float = 0.0,
        counter_rounds: int = 0,
        amount_usd: float = 0.0,
    ) -> None:
        """更新客户画像（交易达成/失败后调用）

        Parameters
        ----------
        client_id : str
            客户唯一标识
        outcome : str
            "accepted" | "rejected" | "expired"
        discount_pct : float
            本次成交折让率 (%)
        counter_rounds : int
            本次还价轮次
        amount_usd : float
            本次成交金额 (USD)
        """
        from sqlalchemy.exc import OperationalError

        for attempt in range(1, _MAX_DB_RETRIES + 1):
            try:
                from database.models import AsyncSessionFactory

                async with AsyncSessionFactory() as session:
                    profile = await session.get(OpponentProfile, client_id)

                    if profile is None:
                        profile = OpponentProfile(
                            client_id=client_id,
                            total_negotiations=1,
                            total_accepted=1 if outcome == "accepted" else 0,
                            total_rejected=1 if outcome != "accepted" else 0,
                            avg_discount_pct=discount_pct,
                            avg_counter_rounds=float(counter_rounds),
                            max_counter_rounds=counter_rounds,
                            total_volume_usd=amount_usd if outcome == "accepted" else 0,
                            risk_tag="normal",
                            last_interaction=datetime.now(timezone.utc),
                        )
                        session.add(profile)
                    else:
                        n = profile.total_negotiations
                        profile.total_negotiations = n + 1

                        if outcome == "accepted":
                            profile.total_accepted += 1
                            profile.total_volume_usd += amount_usd
                            # 滚动平均折让率
                            if profile.total_accepted > 1:
                                profile.avg_discount_pct = round(
                                    (profile.avg_discount_pct * (profile.total_accepted - 1) + discount_pct)
                                    / profile.total_accepted, 2,
                                )
                            else:
                                profile.avg_discount_pct = discount_pct
                        else:
                            profile.total_rejected += 1

                        # 滚动平均还价轮次
                        profile.avg_counter_rounds = round(
                            (profile.avg_counter_rounds * n + counter_rounds) / (n + 1), 2,
                        )
                        profile.max_counter_rounds = max(profile.max_counter_rounds, counter_rounds)
                        profile.last_interaction = datetime.now(timezone.utc)

                    # 重新计算风险标签
                    profile.risk_tag = self._compute_risk_tag(profile)

                    await session.commit()

                logger.info(
                    "画像更新: client=%s outcome=%s tag=%s rounds=%.1f discount=%.1f%%",
                    client_id[:12], outcome, profile.risk_tag,
                    profile.avg_counter_rounds, profile.avg_discount_pct,
                )
                return
            except OperationalError as exc:
                logger.warning("画像更新 OperationalError (attempt %d/%d): %s", attempt, _MAX_DB_RETRIES, exc)
                if attempt >= _MAX_DB_RETRIES:
                    logger.error("画像更新最终失败: %s", exc)
            except Exception as exc:
                logger.debug("画像更新跳过: %s", exc)
                return

    def compute_initial_markup(self, profile: dict[str, Any] | None) -> float:
        """根据客户画像计算初始报价调整系数

        Parameters
        ----------
        profile : dict | None
            客户画像数据

        Returns
        -------
        float
            价格调整系数 (如 0.05 表示上浮 5%, -0.02 表示下调 2%)
        """
        if profile is None:
            return 0.0

        tag = profile.get("risk_tag", "normal")
        if tag == "high_pressure":
            return self.HIGH_PRESSURE_MARKUP
        elif tag == "premium":
            return self.PREMIUM_DISCOUNT
        return 0.0

    @staticmethod
    def _compute_risk_tag(profile: OpponentProfile) -> str:
        """根据画像数据计算风险标签"""
        # 高压客户: 平均还价轮次 > 3 或 拒绝率 > 50%
        if profile.total_negotiations > 0:
            reject_rate = profile.total_rejected / profile.total_negotiations
            if profile.avg_counter_rounds > 3.0 or reject_rate > 0.5:
                return "high_pressure"

        # 优质客户: 成交次数 > 5 且 平均折让率 < 5%
        if profile.total_accepted > 5 and profile.avg_discount_pct < 5.0:
            return "premium"

        return "normal"

    def format_context_prompt(self, profile: dict[str, Any] | None) -> str:
        """将画像格式化为 NegotiatorAgent 的高优 Context

        Parameters
        ----------
        profile : dict | None
            客户画像数据

        Returns
        -------
        str
            注入到谈判引擎的上下文字符串
        """
        if profile is None:
            return ""

        tag = profile.get("risk_tag", "normal")
        markup = self.compute_initial_markup(profile)

        lines = [
            f"[MEMORY] 客户画像: {profile['client_id'][:16]}",
            f"  谈判次数={profile['total_negotiations']} "
            f"成交={profile['total_accepted']} 拒绝={profile['total_rejected']}",
            f"  平均还价轮次={profile['avg_counter_rounds']:.1f} "
            f"平均折让={profile['avg_discount_pct']:.1f}%",
            f"  累计成交额=${profile['total_volume_usd']:.2f}",
            f"  风险标签={tag}",
        ]

        if tag == "high_pressure":
            lines.append(f"  策略: 初始报价上浮 {markup*100:.0f}% (高频压价客户)")
        elif tag == "premium":
            lines.append(f"  策略: 忠诚折扣 {abs(markup)*100:.0f}% (优质客户)")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════════

_profiler: OpponentProfiler | None = None


def get_opponent_profiler() -> OpponentProfiler:
    """获取全局画像管理器单例"""
    global _profiler
    if _profiler is None:
        _profiler = OpponentProfiler()
    return _profiler
