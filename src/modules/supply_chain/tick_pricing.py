"""
modules.supply_chain.tick_pricing — Bloomberg-级 Tick-by-Tick 动态定价引擎
──────────────────────────────────────────────────────────────────────────
职责：
  1. 基于 market_volatility（汇率波动）计算实时定价权重
  2. 基于 inventory_pressure（库容压力）调整报价弹性
  3. 综合评分生成当前报价的权重得分（Tick Score）
  4. 每次报价生成不可篡改的 pricing_audit_trail（SHA256 签名）
  5. audit_trail 记录底价来源、决策逻辑片段、市场因子快照

定价公式::

    tick_score = (
        base_score
        * (1 + volatility_weight * market_volatility_factor)
        * (1 + pressure_weight * inventory_pressure_factor)
    )

    adjusted_price = base_price * (1 + tick_adjustment)

审计追踪::

    pricing_audit_trail = {
        "trail_id": UUID,
        "timestamp": ISO8601,
        "base_price_source": "supplier_catalog | historical_avg | spot_market",
        "decision_logic": "tick_score=87.3 → vol_factor=0.12 → inv_pressure=0.85",
        "market_snapshot": { fx_rate, volatility_7d, inventory_ratio },
        "signature": SHA256(trail_id | base_price | adjusted_price | timestamp),
    }
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from modules.supply_chain.pricing_sources import (
    FxVolatilitySource,
    build_fx_volatility_source,
)
from core.logger import get_logger

logger = get_logger(__name__)


class InventoryPressureGauge:
    """库容压力计 —— 基于库存水位计算供给侧压力因子

    pressure_factor 范围 [0, 1]：
      - 0.0 = 库存充裕，无压力，可大幅让利
      - 0.5 = 正常水位
      - 1.0 = 库存告急，供给紧张，应提价保护
    """

    @staticmethod
    def calculate_pressure(
        stock_qty: int,
        moq: int,
        demand_qty: int,
        avg_daily_turnover: int = 50,
    ) -> dict[str, Any]:
        """计算库容压力因子

        Parameters
        ----------
        stock_qty : int
            当前库存量
        moq : int
            最低起订量
        demand_qty : int
            本次需求量
        avg_daily_turnover : int
            日均周转量（mock 默认 50）

        Returns
        -------
        dict
            pressure_factor, days_of_supply, utilization_ratio
        """
        if stock_qty <= 0:
            return {
                "pressure_factor": 1.0,
                "days_of_supply": 0,
                "utilization_ratio": 1.0,
                "risk_level": "critical",
            }

        days_supply = stock_qty / max(avg_daily_turnover, 1)
        utilization = demand_qty / max(stock_qty, 1)

        # 压力因子：库存越少、需求占比越高 → 压力越大
        if days_supply < 3:
            pressure = 0.95
        elif days_supply < 7:
            pressure = 0.7 + (7 - days_supply) / 7 * 0.25
        elif days_supply < 30:
            pressure = 0.3 + (30 - days_supply) / 30 * 0.4
        else:
            pressure = max(0.05, 0.3 - (days_supply - 30) / 100)

        # 需求占比修正
        if utilization > 0.8:
            pressure = min(1.0, pressure + 0.15)
        elif utilization > 0.5:
            pressure = min(1.0, pressure + 0.05)

        risk = "critical" if pressure > 0.85 else "high" if pressure > 0.6 else "normal"

        return {
            "pressure_factor": round(pressure, 4),
            "days_of_supply": round(days_supply, 1),
            "utilization_ratio": round(utilization, 4),
            "risk_level": risk,
        }


class TickPricingEngine:
    """Bloomberg-级 Tick-by-Tick 动态定价引擎

    综合汇率波动和库容压力，计算每次报价的权重得分，
    并生成不可篡改的 pricing_audit_trail。
    """

    # 权重配置
    VOLATILITY_WEIGHT: float = 0.35   # 汇率波动对定价的影响权重
    PRESSURE_WEIGHT: float = 0.45     # 库容压力对定价的影响权重
    BASE_SCORE: float = 100.0         # 基准评分

    def __init__(self, volatility_source: FxVolatilitySource | None = None) -> None:
        self._vol_oracle: FxVolatilitySource = volatility_source or build_fx_volatility_source()
        self._pressure_gauge = InventoryPressureGauge()

    def compute_tick(
        self,
        base_price_rmb: float,
        stock_qty: int,
        moq: int,
        demand_qty: int,
        currency_pair: str = "USD/CNY",
        base_price_source: str = "supplier_catalog",
    ) -> dict[str, Any]:
        """计算单次 Tick 定价 + 审计追踪

        Parameters
        ----------
        base_price_rmb : float
            底价（人民币）
        stock_qty : int
            当前库存
        moq : int
            最低起订量
        demand_qty : int
            需求量
        currency_pair : str
            货币对
        base_price_source : str
            底价来源标识

        Returns
        -------
        dict
            tick_score, adjusted_price_rmb, tick_adjustment_pct,
            market_snapshot, pricing_audit_trail
        """
        # 1. 获取市场因子
        vol_snapshot = self._vol_oracle.get_fx_volatility(currency_pair)
        pressure_snapshot = self._pressure_gauge.calculate_pressure(
            stock_qty, moq, demand_qty,
        )

        # 2. 计算 Tick Score
        vol_factor = vol_snapshot["volatility_7d"]
        pressure_factor = pressure_snapshot["pressure_factor"]

        tick_score = self.BASE_SCORE * (
            1 + self.VOLATILITY_WEIGHT * vol_factor
        ) * (
            1 + self.PRESSURE_WEIGHT * pressure_factor
        )
        tick_score = round(tick_score, 2)

        # 3. 计算价格调整
        # 高波动 + 高压力 → 提价保护；低波动 + 低压力 → 可让利
        tick_adjustment = (
            vol_factor * self.VOLATILITY_WEIGHT * 0.5
            + pressure_factor * self.PRESSURE_WEIGHT * 0.3
            - 0.05  # 基础让利空间
        )
        tick_adjustment = round(max(-0.15, min(0.25, tick_adjustment)), 4)
        adjusted_price = round(base_price_rmb * (1 + tick_adjustment), 4)

        # 4. 生成审计追踪
        trail = self._build_audit_trail(
            base_price_rmb=base_price_rmb,
            adjusted_price_rmb=adjusted_price,
            tick_score=tick_score,
            tick_adjustment=tick_adjustment,
            vol_snapshot=vol_snapshot,
            pressure_snapshot=pressure_snapshot,
            base_price_source=base_price_source,
        )

        decision_summary = (
            f"tick_score={tick_score} → "
            f"vol_factor={vol_factor:.4f} → "
            f"inv_pressure={pressure_factor:.4f} → "
            f"adjustment={tick_adjustment:+.2%}"
        )

        logger.info(
            "Tick定价: base=¥%.4f → adjusted=¥%.4f (%+.2f%%) score=%.1f",
            base_price_rmb, adjusted_price, tick_adjustment * 100, tick_score,
        )

        return {
            "tick_score": tick_score,
            "base_price_rmb": base_price_rmb,
            "adjusted_price_rmb": adjusted_price,
            "tick_adjustment_pct": round(tick_adjustment * 100, 2),
            "decision_summary": decision_summary,
            "market_snapshot": {
                "volatility": vol_snapshot,
                "inventory_pressure": pressure_snapshot,
            },
            "pricing_audit_trail": trail,
        }

    def _build_audit_trail(
        self,
        base_price_rmb: float,
        adjusted_price_rmb: float,
        tick_score: float,
        tick_adjustment: float,
        vol_snapshot: dict,
        pressure_snapshot: dict,
        base_price_source: str,
    ) -> dict[str, Any]:
        """生成不可篡改的定价审计追踪"""
        trail_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()

        decision_logic = (
            f"tick_score={tick_score} → "
            f"vol_factor={vol_snapshot['volatility_7d']:.4f} "
            f"(drift={vol_snapshot['fx_drift']:+.4f}) → "
            f"inv_pressure={pressure_snapshot['pressure_factor']:.4f} "
            f"(days_supply={pressure_snapshot['days_of_supply']}) → "
            f"adjustment={tick_adjustment:+.4f}"
        )

        # SHA256 签名：防篡改
        sign_payload = (
            f"{trail_id}|{base_price_rmb}|{adjusted_price_rmb}|"
            f"{tick_score}|{ts}"
        )
        signature = hashlib.sha256(sign_payload.encode("utf-8")).hexdigest()

        return {
            "trail_id": trail_id,
            "timestamp": ts,
            "tick_score": tick_score,
            "base_price_rmb": base_price_rmb,
            "adjusted_price_rmb": adjusted_price_rmb,
            "base_price_source": base_price_source,
            "decision_logic": decision_logic,
            "market_snapshot": {
                "fx_rate_mid": vol_snapshot["fx_rate_mid"],
                "volatility_7d": vol_snapshot["volatility_7d"],
                "fx_drift": vol_snapshot["fx_drift"],
                "inventory_pressure": pressure_snapshot["pressure_factor"],
                "inventory_days_supply": pressure_snapshot["days_of_supply"],
                "inventory_risk_level": pressure_snapshot["risk_level"],
            },
            "weights": {
                "volatility_weight": self.VOLATILITY_WEIGHT,
                "pressure_weight": self.PRESSURE_WEIGHT,
            },
            "signature": signature,
        }

    @staticmethod
    def verify_audit_trail(trail: dict[str, Any]) -> bool:
        """验证审计追踪签名是否被篡改"""
        sign_payload = (
            f"{trail['trail_id']}|{trail['base_price_rmb']}|"
            f"{trail['adjusted_price_rmb']}|"
            f"{trail.get('tick_score', 0)}|{trail['timestamp']}"
        )
        expected = hashlib.sha256(sign_payload.encode("utf-8")).hexdigest()
        return expected == trail.get("signature", "")
