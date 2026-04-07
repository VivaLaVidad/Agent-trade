"""
modules.supply_chain.tick_pricing — Bloomberg-级 Tick-by-Tick 动态定价引擎 (事件驱动)
═══════════════════════════════════════════════════════════════════════════════════
职责：
  1. 基于 market_volatility（汇率波动）计算实时定价权重
  2. 基于 inventory_pressure（库容压力）调整报价弹性
  3. 综合评分生成当前报价的权重得分（Tick Score）
  4. 每次报价生成不可篡改的 pricing_audit_trail（SHA256 签名）
  5. **事件驱动**: 主动订阅 MarketDataBus，底价/汇率变动时广播 price_update 事件
  6. **Ticker 绑定**: 所有定价操作必须关联标准化 Ticker ID

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
        "ticker_id": CLAW-ELEC-CAP-100NF50V,
        "timestamp": ISO8601,
        "base_price_source": "supplier_catalog | historical_avg | spot_market",
        "decision_logic": "tick_score=87.3 → vol_factor=0.12 → inv_pressure=0.85",
        "market_snapshot": { fx_rate, volatility_7d, inventory_ratio },
        "signature": SHA256(trail_id | ticker_id | base_price | adjusted_price | timestamp),
    }
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from modules.supply_chain.pricing_sources import (
    FxVolatilitySource,
    build_fx_volatility_source,
)
from core.logger import get_logger
from core.ticker_plant import (
    EventType,
    MarketEvent,
    get_market_bus,
    get_ticker_registry,
)

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
    """Bloomberg-级 Tick-by-Tick 动态定价引擎 (事件驱动)

    综合汇率波动和库容压力，计算每次报价的权重得分，
    并生成不可篡改的 pricing_audit_trail。

    事件驱动增强:
      - compute_tick() 自动向 MarketDataBus 广播 PRICE_UPDATE 事件
      - 支持 Ticker ID 绑定，所有定价操作关联标准化资产编码
      - 检测波动率突变时广播 VOLATILITY_SPIKE 事件
    """

    # 权重配置
    VOLATILITY_WEIGHT: float = 0.35   # 汇率波动对定价的影响权重
    PRESSURE_WEIGHT: float = 0.45     # 库容压力对定价的影响权重
    BASE_SCORE: float = 100.0         # 基准评分

    # 波动率突变阈值
    VOLATILITY_SPIKE_THRESHOLD: float = 0.12  # 7d 波动率超过 12% 视为突变

    def __init__(self, volatility_source: FxVolatilitySource | None = None) -> None:
        self._vol_oracle: FxVolatilitySource = volatility_source or build_fx_volatility_source()
        self._pressure_gauge = InventoryPressureGauge()
        self._last_volatility: dict[str, float] = {}  # ticker_id → last vol_7d

    def compute_tick(
        self,
        base_price_rmb: float,
        stock_qty: int,
        moq: int,
        demand_qty: int,
        currency_pair: str = "USD/CNY",
        base_price_source: str = "supplier_catalog",
        ticker_id: str = "",
        category: str = "",
        sku_name: str = "",
    ) -> dict[str, Any]:
        """计算单次 Tick 定价 + 审计追踪 + 事件广播

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
        ticker_id : str
            标准化 Ticker ID (如为空则自动解析)
        category : str
            品类 (用于自动解析 Ticker)
        sku_name : str
            SKU 名称 (用于自动解析 Ticker)

        Returns
        -------
        dict
            tick_score, adjusted_price_rmb, tick_adjustment_pct,
            ticker_id, market_snapshot, pricing_audit_trail
        """
        # 0. 解析 Ticker ID
        if not ticker_id and (category or sku_name):
            registry = get_ticker_registry()
            ticker = registry.resolve(category or "unknown", sku_name or "")
            ticker_id = ticker.ticker_id

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
        tick_adjustment = (
            vol_factor * self.VOLATILITY_WEIGHT * 0.5
            + pressure_factor * self.PRESSURE_WEIGHT * 0.3
            - 0.05  # 基础让利空间
        )
        tick_adjustment = round(max(-0.15, min(0.25, tick_adjustment)), 4)
        adjusted_price = round(base_price_rmb * (1 + tick_adjustment), 4)

        # 4. 检测波动率突变
        is_spike = vol_factor >= self.VOLATILITY_SPIKE_THRESHOLD
        prev_vol = self._last_volatility.get(ticker_id, 0)
        vol_delta = abs(vol_factor - prev_vol)
        if ticker_id:
            self._last_volatility[ticker_id] = vol_factor

        # 5. 生成审计追踪
        trail = self._build_audit_trail(
            base_price_rmb=base_price_rmb,
            adjusted_price_rmb=adjusted_price,
            tick_score=tick_score,
            tick_adjustment=tick_adjustment,
            vol_snapshot=vol_snapshot,
            pressure_snapshot=pressure_snapshot,
            base_price_source=base_price_source,
            ticker_id=ticker_id,
            is_spike=is_spike,
        )

        decision_summary = (
            f"tick_score={tick_score} → "
            f"vol_factor={vol_factor:.4f} → "
            f"inv_pressure={pressure_factor:.4f} → "
            f"adjustment={tick_adjustment:+.2%}"
        )

        logger.info(
            "Tick定价: ticker=%s base=¥%.4f → adjusted=¥%.4f (%+.2f%%) score=%.1f%s",
            ticker_id or "N/A", base_price_rmb, adjusted_price,
            tick_adjustment * 100, tick_score,
            " [SPIKE]" if is_spike else "",
        )

        result = {
            "tick_score": tick_score,
            "ticker_id": ticker_id,
            "base_price_rmb": base_price_rmb,
            "adjusted_price_rmb": adjusted_price,
            "tick_adjustment_pct": round(tick_adjustment * 100, 2),
            "decision_summary": decision_summary,
            "is_volatility_spike": is_spike,
            "volatility_delta": round(vol_delta, 4),
            "market_snapshot": {
                "volatility": vol_snapshot,
                "inventory_pressure": pressure_snapshot,
            },
            "pricing_audit_trail": trail,
        }

        # 6. 异步广播事件 (fire-and-forget)
        if ticker_id:
            self._fire_events(ticker_id, adjusted_price, base_price_rmb, is_spike, vol_snapshot)

        return result

    def _fire_events(
        self,
        ticker_id: str,
        adjusted_price: float,
        base_price: float,
        is_spike: bool,
        vol_snapshot: dict,
    ) -> None:
        """异步广播市场事件 (非阻塞)"""
        bus = get_market_bus()

        # 价格更新事件
        price_event = MarketEvent(
            event_type=EventType.PRICE_UPDATE,
            ticker_id=ticker_id,
            data={
                "new_price_rmb": adjusted_price,
                "base_price_rmb": base_price,
                "fx_rate_mid": vol_snapshot.get("fx_rate_mid", 7.25),
                "volatility_7d": vol_snapshot.get("volatility_7d", 0),
            },
        )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(bus.publish(price_event))
        except RuntimeError:
            # 非异步上下文 — 跳过广播
            pass

        # 波动率突变事件
        if is_spike:
            spike_event = MarketEvent(
                event_type=EventType.VOLATILITY_SPIKE,
                ticker_id=ticker_id,
                data={
                    "volatility_7d": vol_snapshot.get("volatility_7d", 0),
                    "fx_drift": vol_snapshot.get("fx_drift", 0),
                    "fx_rate_mid": vol_snapshot.get("fx_rate_mid", 7.25),
                    "threshold": self.VOLATILITY_SPIKE_THRESHOLD,
                    "severity": "high" if vol_snapshot.get("volatility_7d", 0) > 0.15 else "medium",
                },
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(bus.publish(spike_event))
            except RuntimeError:
                pass

    def _build_audit_trail(
        self,
        base_price_rmb: float,
        adjusted_price_rmb: float,
        tick_score: float,
        tick_adjustment: float,
        vol_snapshot: dict,
        pressure_snapshot: dict,
        base_price_source: str,
        ticker_id: str = "",
        is_spike: bool = False,
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
        if is_spike:
            decision_logic += " [VOLATILITY_SPIKE]"

        # SHA256 签名：防篡改 (含 ticker_id)
        sign_payload = (
            f"{trail_id}|{ticker_id}|{base_price_rmb}|{adjusted_price_rmb}|"
            f"{tick_score}|{ts}"
        )
        signature = hashlib.sha256(sign_payload.encode("utf-8")).hexdigest()

        return {
            "trail_id": trail_id,
            "ticker_id": ticker_id,
            "timestamp": ts,
            "tick_score": tick_score,
            "base_price_rmb": base_price_rmb,
            "adjusted_price_rmb": adjusted_price_rmb,
            "base_price_source": base_price_source,
            "decision_logic": decision_logic,
            "is_volatility_spike": is_spike,
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
            f"{trail['trail_id']}|{trail.get('ticker_id', '')}|{trail['base_price_rmb']}|"
            f"{trail['adjusted_price_rmb']}|"
            f"{trail.get('tick_score', 0)}|{trail['timestamp']}"
        )
        expected = hashlib.sha256(sign_payload.encode("utf-8")).hexdigest()
        return expected == trail.get("signature", "")
