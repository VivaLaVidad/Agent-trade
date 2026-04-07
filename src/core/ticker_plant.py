"""
core.ticker_plant — Claw Ticker Plant (统一资产编码 + 实时数据总线)
═══════════════════════════════════════════════════════════════════
对标 Bloomberg Terminal 的 Ticker 体系：

1. **AssetTicker** — 将非标商品名映射为唯一标准化 Ticker ID
   (如 "MLCC 贴片电容 100nF" → ``CLAW-ELEC-MLCC-100NF``)
2. **TickerRegistry** — 全局 Ticker 注册表，支持模糊查找 / 精确解析
3. **MarketDataBus** — 分布式事件总线 (redis.asyncio Pub/Sub)
   - 生产模式: Redis Pub/Sub (topic:market_updates)
   - 降级模式: 进程内 asyncio 广播 (无 Redis 时自动降级)
4. **MarketEvent** — 标准化市场事件模型

工程约束:
  - 谈判引擎 / 账本 / 阶梯报价 严禁传递原始文本，必须使用 Ticker ID
  - 所有 PG 操作包裹 OperationalError 重试
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

from core.logger import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════
#  Asset Ticker — 统一资产编码
# ═══════════════════════════════════════════════════════════════

class AssetClass(str, Enum):
    """资产大类"""
    ELECTRONIC = "ELEC"
    MECHANICAL = "MECH"
    CHEMICAL = "CHEM"
    OPTICAL = "OPTO"
    MATERIAL = "MATL"
    CONNECTOR = "CONN"
    PCB = "PCB"
    UNKNOWN = "UNKN"


# 品类 → (AssetClass, 标准缩写) 映射表
_CATEGORY_MAP: dict[str, tuple[AssetClass, str]] = {
    "capacitor":  (AssetClass.ELECTRONIC, "CAP"),
    "resistor":   (AssetClass.ELECTRONIC, "RES"),
    "inductor":   (AssetClass.ELECTRONIC, "IND"),
    "ic":         (AssetClass.ELECTRONIC, "IC"),
    "led":        (AssetClass.OPTICAL, "LED"),
    "connector":  (AssetClass.CONNECTOR, "CONN"),
    "pcb":        (AssetClass.PCB, "PCB"),
    "diode":      (AssetClass.ELECTRONIC, "DIODE"),
    "transistor": (AssetClass.ELECTRONIC, "XSTR"),
    "relay":      (AssetClass.ELECTRONIC, "RELAY"),
    "sensor":     (AssetClass.ELECTRONIC, "SENS"),
    "motor":      (AssetClass.MECHANICAL, "MOTOR"),
    "bearing":    (AssetClass.MECHANICAL, "BRG"),
    "cable":      (AssetClass.ELECTRONIC, "CABLE"),
    "fuse":       (AssetClass.ELECTRONIC, "FUSE"),
    "crystal":    (AssetClass.ELECTRONIC, "XTAL"),
    "transformer": (AssetClass.ELECTRONIC, "XFMR"),
    "battery":    (AssetClass.ELECTRONIC, "BATT"),
    "switch":     (AssetClass.ELECTRONIC, "SW"),
    "regulator":  (AssetClass.ELECTRONIC, "REG"),
}

# 非标名称 → 标准品类的模糊映射
_FUZZY_ALIASES: dict[str, str] = {
    "mlcc": "capacitor", "贴片电容": "capacitor", "ceramic capacitor": "capacitor",
    "电容": "capacitor", "cap": "capacitor", "capacitors": "capacitor",
    "电阻": "resistor", "resistors": "resistor", "res": "resistor",
    "电感": "inductor", "inductors": "inductor",
    "芯片": "ic", "chip": "ic", "mcu": "ic", "microcontroller": "ic",
    "发光二极管": "led", "leds": "led", "light emitting diode": "led",
    "接插件": "connector", "connectors": "connector",
    "印刷电路板": "pcb", "circuit board": "pcb",
    "二极管": "diode", "diodes": "diode",
    "三极管": "transistor", "mosfet": "transistor",
    "继电器": "relay", "relays": "relay",
    "传感器": "sensor", "sensors": "sensor",
    "电机": "motor", "motors": "motor",
    "轴承": "bearing", "bearings": "bearing",
    "线缆": "cable", "cables": "cable", "wire": "cable",
    "保险丝": "fuse", "fuses": "fuse",
    "晶振": "crystal", "oscillator": "crystal",
    "变压器": "transformer", "transformers": "transformer",
    "电池": "battery", "batteries": "battery",
    "开关": "switch", "switches": "switch",
    "稳压器": "regulator", "voltage regulator": "regulator",
    "ldo": "regulator", "ldo voltage regulator": "regulator",
}

# 规格值标准化正则
_SPEC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*nf", re.I), r"\1NF"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*uf", re.I), r"\1UF"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*pf", re.I), r"\1PF"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*v\b", re.I), r"\1V"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*k(?:ohm)?", re.I), r"\1K"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*ohm", re.I), r"\1R"),
    (re.compile(r"smd[- ]?(\d{4})", re.I), r"SMD\1"),
    (re.compile(r"(\d{4})\s*package", re.I), r"SMD\1"),
]


@dataclass(frozen=True)
class AssetTicker:
    """标准化资产 Ticker

    格式: CLAW-{ASSET_CLASS}-{CATEGORY}-{SPEC_HASH}
    示例: CLAW-ELEC-CAP-100NF50V0805
    """
    ticker_id: str
    asset_class: AssetClass
    category_code: str
    spec_tag: str
    raw_category: str = ""
    raw_name: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TickerRegistry:
    """全局 Ticker 注册表 — 线程安全的单例注册中心

    职责:
      - 将非标商品名解析为标准 AssetTicker
      - 缓存已注册 Ticker，避免重复计算
      - 支持模糊查找 / 精确解析
    """

    def __init__(self) -> None:
        self._tickers: dict[str, AssetTicker] = {}
        self._name_index: dict[str, str] = {}  # normalized_name → ticker_id

    def resolve(
        self,
        category: str,
        name: str = "",
        specs: dict[str, Any] | None = None,
    ) -> AssetTicker:
        """将非标品类/名称/规格解析为标准 Ticker

        Parameters
        ----------
        category : str
            品类 (如 "capacitor", "MLCC 贴片电容")
        name : str
            SKU 名称 (如 "100nF 50V 0805 MLCC")
        specs : dict | None
            规格参数 (如 {"voltage": "50V", "package": "0805"})

        Returns
        -------
        AssetTicker
            标准化 Ticker 对象
        """
        # 1. 标准化品类
        std_category = self._normalize_category(category)
        asset_class, cat_code = _CATEGORY_MAP.get(
            std_category, (AssetClass.UNKNOWN, "GEN"),
        )

        # 2. 提取规格标签
        spec_tag = self._build_spec_tag(name, specs)

        # 3. 构建 Ticker ID
        ticker_id = f"CLAW-{asset_class.value}-{cat_code}-{spec_tag}"

        # 4. 缓存
        if ticker_id not in self._tickers:
            ticker = AssetTicker(
                ticker_id=ticker_id,
                asset_class=asset_class,
                category_code=cat_code,
                spec_tag=spec_tag,
                raw_category=category,
                raw_name=name,
            )
            self._tickers[ticker_id] = ticker
            norm_key = f"{std_category}:{spec_tag}".lower()
            self._name_index[norm_key] = ticker_id
            logger.debug("Ticker 注册: %s ← %s / %s", ticker_id, category, name[:40])

        return self._tickers[ticker_id]

    def lookup(self, ticker_id: str) -> AssetTicker | None:
        """精确查找 Ticker"""
        return self._tickers.get(ticker_id)

    def search(self, keyword: str, limit: int = 10) -> list[AssetTicker]:
        """模糊搜索 Ticker"""
        kw = keyword.lower()
        results = []
        for tid, ticker in self._tickers.items():
            if (kw in tid.lower()
                    or kw in ticker.raw_name.lower()
                    or kw in ticker.raw_category.lower()):
                results.append(ticker)
                if len(results) >= limit:
                    break
        return results

    def all_tickers(self) -> list[AssetTicker]:
        """返回所有已注册 Ticker"""
        return list(self._tickers.values())

    def _normalize_category(self, raw: str) -> str:
        """将非标品类名映射为标准品类键"""
        lower = raw.strip().lower()
        if lower in _CATEGORY_MAP:
            return lower
        if lower in _FUZZY_ALIASES:
            return _FUZZY_ALIASES[lower]
        # 尝试子串匹配
        for alias, std in _FUZZY_ALIASES.items():
            if alias in lower or lower in alias:
                return std
        return lower

    def _build_spec_tag(self, name: str, specs: dict[str, Any] | None) -> str:
        """从名称和规格参数中提取标准化规格标签"""
        parts: list[str] = []

        # 从名称中提取规格值
        combined = name
        if specs:
            combined += " " + " ".join(str(v) for v in specs.values())

        for pattern, replacement in _SPEC_PATTERNS:
            for m in pattern.finditer(combined):
                parts.append(pattern.sub(replacement, m.group()))

        if not parts:
            # 无法提取规格 → 使用名称哈希
            h = hashlib.md5(name.encode()).hexdigest()[:8].upper()
            return f"X{h}"

        # 去重 + 排序 → 确定性标签
        unique = sorted(set(parts))
        return "".join(unique)


# ═══════════════════════════════════════════════════════════════
#  Market Event — 标准化市场事件
# ═══════════════════════════════════════════════════════════════

class EventType(str, Enum):
    """市场事件类型"""
    PRICE_UPDATE = "price_update"
    FX_TICK = "fx_tick"
    VOLATILITY_SPIKE = "volatility_spike"
    INVENTORY_ALERT = "inventory_alert"
    NEGOTIATION_UPDATE = "negotiation_update"
    REG_DENIED = "reg_denied"
    DOCUMENT_GENERATED = "document_generated"
    HEDGE_LOCKED = "hedge_locked"
    PENDING_REVIEW = "pending_review"


@dataclass
class MarketEvent:
    """标准化市场事件"""
    event_type: EventType
    ticker_id: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: hashlib.md5(
        f"{time.time()}{id(object())}".encode()
    ).hexdigest()[:12])

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "ticker_id": self.ticker_id,
            "data": self.data,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MarketEvent:
        return cls(
            event_type=EventType(d["event_type"]),
            ticker_id=d["ticker_id"],
            data=d.get("data", {}),
            timestamp=d.get("timestamp", time.time()),
            event_id=d.get("event_id", ""),
        )


# ═══════════════════════════════════════════════════════════════
#  MarketDataBus — 分布式事件总线
# ═══════════════════════════════════════════════════════════════

# 订阅回调类型: async def handler(event: MarketEvent) -> None
EventHandler = Callable[[MarketEvent], Awaitable[None]]

_REDIS_CHANNEL = "claw:market_updates"


class MarketDataBus:
    """分布式市场数据总线

    生产模式: Redis Pub/Sub (需配置 REDIS_URL 环境变量)
    降级模式: 进程内 asyncio 广播 (无 Redis 时自动降级)

    用法::

        bus = get_market_bus()

        # 发布
        await bus.publish(MarketEvent(
            event_type=EventType.PRICE_UPDATE,
            ticker_id="CLAW-ELEC-CAP-100NF50V",
            data={"new_price": 0.52, "old_price": 0.50},
        ))

        # 订阅
        async def on_price(event: MarketEvent):
            print(f"Price update: {event.ticker_id}")

        bus.subscribe("CLAW-ELEC-CAP-*", on_price)
    """

    def __init__(self) -> None:
        self._local_handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._global_handlers: list[EventHandler] = []
        self._redis = None
        self._redis_sub = None
        self._redis_task: asyncio.Task | None = None
        self._event_log: list[MarketEvent] = []
        self._max_log_size = 500
        self._started = False

    async def start(self) -> None:
        """启动总线 (尝试连接 Redis，失败则降级)"""
        if self._started:
            return
        self._started = True

        import os
        redis_url = os.environ.get("REDIS_URL", "").strip()
        if redis_url:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_connect_timeout=3,
                )
                await self._redis.ping()
                self._redis_sub = self._redis.pubsub()
                await self._redis_sub.subscribe(_REDIS_CHANNEL)
                self._redis_task = asyncio.create_task(self._redis_listener())
                logger.info("MarketDataBus: Redis Pub/Sub 已连接 (%s)", redis_url)
                return
            except Exception as exc:
                logger.warning("MarketDataBus: Redis 连接失败，降级进程内模式: %s", exc)
                self._redis = None
                self._redis_sub = None

        logger.info("MarketDataBus: 进程内广播模式 (无 Redis)")

    async def stop(self) -> None:
        """停止总线"""
        self._started = False
        if self._redis_task and not self._redis_task.done():
            self._redis_task.cancel()
            try:
                await self._redis_task
            except asyncio.CancelledError:
                pass
        if self._redis_sub:
            try:
                await self._redis_sub.unsubscribe(_REDIS_CHANNEL)
                await self._redis_sub.close()
            except Exception:
                pass
        if self._redis:
            try:
                await self._redis.close()
            except Exception:
                pass
        logger.info("MarketDataBus: 已停止")

    async def publish(self, event: MarketEvent) -> None:
        """发布市场事件

        Parameters
        ----------
        event : MarketEvent
            标准化市场事件
        """
        # 记录事件日志
        self._event_log.append(event)
        if len(self._event_log) > self._max_log_size:
            self._event_log = self._event_log[-self._max_log_size:]

        # Redis 广播
        if self._redis:
            try:
                import json
                await self._redis.publish(_REDIS_CHANNEL, json.dumps(event.to_dict()))
            except Exception as exc:
                logger.warning("Redis publish 失败，回退本地广播: %s", exc)
                await self._dispatch_local(event)
        else:
            await self._dispatch_local(event)

        logger.debug(
            "MarketDataBus: 发布 %s ticker=%s",
            event.event_type.value, event.ticker_id,
        )

    def subscribe(
        self,
        ticker_pattern: str,
        handler: EventHandler,
    ) -> None:
        """订阅指定 Ticker 的事件

        Parameters
        ----------
        ticker_pattern : str
            Ticker ID 或通配符模式 (如 "CLAW-ELEC-CAP-*" 或 "*" 表示全部)
        handler : EventHandler
            异步回调函数
        """
        if ticker_pattern == "*":
            self._global_handlers.append(handler)
        else:
            self._local_handlers[ticker_pattern].append(handler)
        logger.debug("MarketDataBus: 订阅 pattern=%s", ticker_pattern)

    def unsubscribe(
        self,
        ticker_pattern: str,
        handler: EventHandler,
    ) -> None:
        """取消订阅"""
        if ticker_pattern == "*":
            if handler in self._global_handlers:
                self._global_handlers.remove(handler)
        else:
            handlers = self._local_handlers.get(ticker_pattern, [])
            if handler in handlers:
                handlers.remove(handler)

    def get_recent_events(self, limit: int = 50) -> list[MarketEvent]:
        """获取最近的事件日志"""
        return list(self._event_log[-limit:])

    def get_ticker_events(self, ticker_id: str, limit: int = 20) -> list[MarketEvent]:
        """获取指定 Ticker 的最近事件"""
        return [
            e for e in reversed(self._event_log)
            if e.ticker_id == ticker_id
        ][:limit]

    async def _dispatch_local(self, event: MarketEvent) -> None:
        """进程内事件分发"""
        tasks: list[asyncio.Task] = []

        # 全局处理器
        for handler in self._global_handlers:
            tasks.append(asyncio.create_task(self._safe_call(handler, event)))

        # 精确匹配
        for handler in self._local_handlers.get(event.ticker_id, []):
            tasks.append(asyncio.create_task(self._safe_call(handler, event)))

        # 通配符匹配
        for pattern, handlers in self._local_handlers.items():
            if pattern == event.ticker_id:
                continue  # 已处理
            if self._match_pattern(pattern, event.ticker_id):
                for handler in handlers:
                    tasks.append(asyncio.create_task(self._safe_call(handler, event)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _redis_listener(self) -> None:
        """Redis Pub/Sub 监听循环"""
        import json
        try:
            async for message in self._redis_sub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    event = MarketEvent.from_dict(data)
                    await self._dispatch_local(event)
                except Exception as exc:
                    logger.warning("Redis 消息解析失败: %s", exc)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Redis listener 异常退出: %s", exc)

    @staticmethod
    def _match_pattern(pattern: str, ticker_id: str) -> bool:
        """简单通配符匹配 (支持尾部 *)"""
        if pattern.endswith("*"):
            return ticker_id.startswith(pattern[:-1])
        return pattern == ticker_id

    @staticmethod
    async def _safe_call(handler: EventHandler, event: MarketEvent) -> None:
        """安全调用处理器"""
        try:
            await handler(event)
        except Exception as exc:
            logger.error("事件处理器异常: %s — %s", type(exc).__name__, exc)


# ═══════════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════════

_ticker_registry: TickerRegistry | None = None
_market_bus: MarketDataBus | None = None


def get_ticker_registry() -> TickerRegistry:
    """获取全局 Ticker 注册表单例"""
    global _ticker_registry
    if _ticker_registry is None:
        _ticker_registry = TickerRegistry()
    return _ticker_registry


def get_market_bus() -> MarketDataBus:
    """获取全局市场数据总线单例"""
    global _market_bus
    if _market_bus is None:
        _market_bus = MarketDataBus()
    return _market_bus
