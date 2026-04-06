"""
modules.supply_chain.pricing_sources — 汇率/波动率数据源（可插拔）
──────────────────────────────────────────────────────────────
生产建议：
  - mock：默认，确定性回归与无网环境
  - http：设置 TS_FX_API_URL（返回 JSON 含 volatility_7d, fx_drift, fx_rate_mid）
环境变量：
  TS_FX_VOLATILITY_SOURCE = mock | http
  TS_FX_API_URL            = https://...
  TS_FX_API_TIMEOUT_SEC    = 5.0
"""

from __future__ import annotations

import os
import random
from typing import Any, Protocol, runtime_checkable

from core.logger import get_logger

logger = get_logger(__name__)


@runtime_checkable
class FxVolatilitySource(Protocol):
    """波动率快照数据源（工业扩展点：接入 exchangerate-api、Bloomberg 等）"""

    def get_fx_volatility(self, currency_pair: str = "USD/CNY") -> dict[str, Any]:
        ...


class MockFxVolatilitySource:
    """可复现特征的 Mock（非加密学随机，仅演示波动形态）"""

    @staticmethod
    def get_fx_volatility(currency_pair: str = "USD/CNY") -> dict[str, Any]:
        vol_7d = round(random.uniform(0.02, 0.18), 4)
        drift = round(random.uniform(-0.5, 0.5), 4)
        mid_rate = 7.25 + random.uniform(-0.15, 0.15)
        return {
            "currency_pair": currency_pair,
            "volatility_7d": vol_7d,
            "fx_drift": drift,
            "fx_rate_mid": round(mid_rate, 4),
            "confidence": round(random.uniform(0.85, 0.99), 3),
            "source": "mock_oracle",
        }


class HttpFxVolatilitySource:
    """HTTP JSON 源；失败时降级 Mock 并打警告（生产可接内部风控服务）"""

    def __init__(self, url: str | None, timeout_sec: float) -> None:
        self._url = (url or "").strip()
        self._timeout = timeout_sec
        self._fallback = MockFxVolatilitySource()

    def get_fx_volatility(self, currency_pair: str = "USD/CNY") -> dict[str, Any]:
        if not self._url:
            logger.warning("TS_FX_API_URL 未配置，波动率回退 mock")
            return self._fallback.get_fx_volatility(currency_pair)
        try:
            import httpx

            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(
                    self._url,
                    params={"pair": currency_pair},
                    headers={"Accept": "application/json"},
                )
                r.raise_for_status()
                data = r.json()
            out = {
                "currency_pair": currency_pair,
                "volatility_7d": float(data.get("volatility_7d", data.get("vol_7d", 0.08))),
                "fx_drift": float(data.get("fx_drift", data.get("drift", 0.0))),
                "fx_rate_mid": float(data.get("fx_rate_mid", data.get("mid", 7.25))),
                "confidence": float(data.get("confidence", 0.95)),
                "source": "http",
            }
            return out
        except Exception as exc:
            logger.warning("HTTP 波动率拉取失败，回退 mock: %s", exc)
            snap = self._fallback.get_fx_volatility(currency_pair)
            snap["source"] = "mock_oracle_fallback"
            return snap


def build_fx_volatility_source() -> FxVolatilitySource:
    mode = os.environ.get("TS_FX_VOLATILITY_SOURCE", "mock").strip().lower()
    timeout = float(os.environ.get("TS_FX_API_TIMEOUT_SEC", "5.0"))
    if mode == "http":
        return HttpFxVolatilitySource(os.environ.get("TS_FX_API_URL"), timeout)
    return MockFxVolatilitySource()
