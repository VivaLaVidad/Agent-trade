"""
modules.supply_chain.fx_service — 外汇汇率服务（Mock）
───────────────────────────────────────────────────────
职责：
  1. 提供实时汇率查询接口（当前为 mock 数据，接口设计兼容未来接入真实 API）
  2. 多币种换算：RMB → USD / EUR / GBP / JPY 等
  3. 含运费估算辅助方法
"""

from __future__ import annotations

import random
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

_MOCK_RATES: dict[str, float] = {
    "USD/CNY": 7.25,
    "EUR/CNY": 7.90,
    "GBP/CNY": 9.20,
    "JPY/CNY": 0.048,
    "INR/CNY": 0.087,
    "NGN/CNY": 0.0047,
    "BRL/CNY": 1.45,
    "THB/CNY": 0.20,
    "VND/CNY": 0.00029,
    "KES/CNY": 0.056,
}

_SHIPPING_ESTIMATES: dict[str, dict[str, float]] = {
    "Asia": {"FOB": 0.0, "CIF": 0.03},
    "Africa": {"FOB": 0.0, "CIF": 0.08},
    "Europe": {"FOB": 0.0, "CIF": 0.05},
    "South America": {"FOB": 0.0, "CIF": 0.07},
    "North America": {"FOB": 0.0, "CIF": 0.04},
    "Middle East": {"FOB": 0.0, "CIF": 0.06},
    "default": {"FOB": 0.0, "CIF": 0.06},
}

_REGION_MAP: dict[str, str] = {
    "Nigeria": "Africa", "Kenya": "Africa", "Ghana": "Africa", "South Africa": "Africa",
    "India": "Asia", "Thailand": "Asia", "Vietnam": "Asia", "Indonesia": "Asia",
    "Pakistan": "Asia", "Bangladesh": "Asia", "Philippines": "Asia", "Malaysia": "Asia",
    "Brazil": "South America", "Colombia": "South America", "Mexico": "North America",
    "Germany": "Europe", "France": "Europe", "UK": "Europe", "Poland": "Europe",
    "Turkey": "Middle East", "UAE": "Middle East", "Saudi Arabia": "Middle East",
    "USA": "North America", "Canada": "North America",
}


class FxRateService:
    """外汇汇率与运费估算服务

    当前使用 mock 数据，接口签名兼容未来接入
    exchange-rate-api.com / openexchangerates.org 等真实 API。
    """

    def get_rate(self, from_currency: str = "CNY", to_currency: str = "USD") -> float:
        """获取汇率

        Parameters
        ----------
        from_currency : str
        to_currency : str

        Returns
        -------
        float
            1 单位 from_currency 兑换多少 to_currency
        """
        pair = f"{to_currency}/{from_currency}"
        rate = _MOCK_RATES.get(pair)
        if rate:
            return round(1.0 / rate, 6)

        reverse_pair = f"{from_currency}/{to_currency}"
        reverse_rate = _MOCK_RATES.get(reverse_pair)
        if reverse_rate:
            return round(reverse_rate, 6)

        if from_currency == to_currency:
            return 1.0

        noise = random.uniform(-0.02, 0.02)
        return round(0.14 + noise, 6)

    def convert(
        self, amount: float, from_currency: str = "CNY", to_currency: str = "USD",
    ) -> float:
        """金额换算"""
        rate = self.get_rate(from_currency, to_currency)
        return round(amount * rate, 2)

    def estimate_shipping_pct(
        self, destination_country: str, shipping_term: str = "CIF",
    ) -> float:
        """估算运费占比（占货值百分比）"""
        region = _REGION_MAP.get(destination_country, "")
        estimates = _SHIPPING_ESTIMATES.get(region, _SHIPPING_ESTIMATES["default"])
        return estimates.get(shipping_term, 0.05)

    def calculate_landed_cost(
        self,
        unit_price_rmb: float,
        quantity: int,
        destination_country: str,
        shipping_term: str = "CIF",
    ) -> dict[str, Any]:
        """计算落地成本（含汇率 + 运费估算）"""
        total_rmb = unit_price_rmb * quantity
        total_usd = self.convert(total_rmb, "CNY", "USD")
        shipping_pct = self.estimate_shipping_pct(destination_country, shipping_term)
        shipping_usd = round(total_usd * shipping_pct, 2)
        landed_usd = round(total_usd + shipping_usd, 2)
        fx_rate = self.get_rate("CNY", "USD")

        return {
            "unit_price_rmb": unit_price_rmb,
            "total_rmb": round(total_rmb, 2),
            "total_usd": total_usd,
            "shipping_usd": shipping_usd,
            "landed_usd": landed_usd,
            "fx_rate": fx_rate,
            "shipping_term": shipping_term,
        }
