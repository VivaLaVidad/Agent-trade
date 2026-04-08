"""
database.mock_inventory — 本地模拟库存（Local-First 闭环策略）
═══════════════════════════════════════════════════════════════
预置 50 条工业电子元件 SKU，供 LocalInventoryNode 查询。
当本地库存命中且利润率 > 5% 时，跳过外部撮合直接进入谈判。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class InventoryItem:
    """单条库存记录"""
    sku_id: str
    sku_name: str
    category: str
    stock_qty: int
    cost_price: float          # 成本价 USD
    suggested_sell_price: float # 建议售价 USD
    location: str
    specs: dict = field(default_factory=dict)
    is_un_certified: bool = True
    is_rcep_eligible: bool = True


# ═══════════════════════════════════════════════════════════════
#  预置 50 条工业电子元件 SKU
# ═══════════════════════════════════════════════════════════════
_PRESET_INVENTORY: list[InventoryItem] = [
    # ── 电容 (Capacitors) ──
    InventoryItem("SKU-CAP-001", "100nF MLCC 0402", "capacitor", 50000, 0.008, 0.015, "SZ-A1", {"capacitance": "100nF", "package": "0402"}),
    InventoryItem("SKU-CAP-002", "10uF Tantalum SMD", "capacitor", 20000, 0.035, 0.065, "SZ-A1", {"capacitance": "10uF", "type": "tantalum"}),
    InventoryItem("SKU-CAP-003", "1uF MLCC 0603", "capacitor", 80000, 0.005, 0.010, "SZ-A2", {"capacitance": "1uF", "package": "0603"}),
    InventoryItem("SKU-CAP-004", "22uF Electrolytic", "capacitor", 15000, 0.020, 0.038, "GZ-B1", {"capacitance": "22uF", "type": "electrolytic"}),
    InventoryItem("SKU-CAP-005", "470pF COG 0402", "capacitor", 100000, 0.003, 0.006, "SZ-A1", {"capacitance": "470pF", "dielectric": "COG"}),
    InventoryItem("SKU-CAP-006", "4.7uF X5R 0805", "capacitor", 60000, 0.012, 0.022, "SZ-A2", {"capacitance": "4.7uF", "dielectric": "X5R"}),
    InventoryItem("SKU-CAP-007", "100uF Aluminum", "capacitor", 10000, 0.045, 0.085, "GZ-B1", {"capacitance": "100uF", "type": "aluminum"}),
    # ── 电阻 (Resistors) ──
    InventoryItem("SKU-RES-001", "10K Ohm 0402 1%", "resistor", 200000, 0.002, 0.004, "SZ-A1", {"resistance": "10K", "tolerance": "1%"}),
    InventoryItem("SKU-RES-002", "4.7K Ohm 0603 5%", "resistor", 150000, 0.002, 0.004, "SZ-A1", {"resistance": "4.7K", "tolerance": "5%"}),
    InventoryItem("SKU-RES-003", "100 Ohm 0805 1%", "resistor", 120000, 0.003, 0.005, "SZ-A2", {"resistance": "100", "tolerance": "1%"}),
    InventoryItem("SKU-RES-004", "1M Ohm 0402 5%", "resistor", 180000, 0.002, 0.003, "SZ-A1", {"resistance": "1M", "tolerance": "5%"}),
    InventoryItem("SKU-RES-005", "220 Ohm 1206 1%", "resistor", 90000, 0.004, 0.007, "GZ-B1", {"resistance": "220", "tolerance": "1%"}),
    InventoryItem("SKU-RES-006", "47K Ohm 0603 1%", "resistor", 160000, 0.002, 0.004, "SZ-A2", {"resistance": "47K", "tolerance": "1%"}),
    InventoryItem("SKU-RES-007", "0 Ohm Jumper 0402", "resistor", 500000, 0.001, 0.002, "SZ-A1", {"resistance": "0", "type": "jumper"}),
    # ── 芯片 (ICs) ──
    InventoryItem("SKU-IC-001", "STM32F103C8T6", "mcu", 5000, 2.50, 4.80, "SZ-C1", {"core": "ARM Cortex-M3", "flash": "64KB"}),
    InventoryItem("SKU-IC-002", "ESP32-WROOM-32E", "mcu", 8000, 1.80, 3.50, "SZ-C1", {"core": "Xtensa LX6", "wifi": "802.11b/g/n"}),
    InventoryItem("SKU-IC-003", "ATmega328P-AU", "mcu", 3000, 1.20, 2.30, "GZ-B2", {"core": "AVR", "flash": "32KB"}),
    InventoryItem("SKU-IC-004", "NE555 Timer SOIC-8", "analog_ic", 25000, 0.08, 0.15, "SZ-A2", {"type": "timer", "package": "SOIC-8"}),
    InventoryItem("SKU-IC-005", "LM7805 Regulator TO-220", "power_ic", 12000, 0.15, 0.28, "GZ-B1", {"output": "5V", "package": "TO-220"}),
    InventoryItem("SKU-IC-006", "AMS1117-3.3 SOT-223", "power_ic", 30000, 0.06, 0.12, "SZ-A1", {"output": "3.3V", "package": "SOT-223"}),
    InventoryItem("SKU-IC-007", "CH340G USB-UART", "interface_ic", 15000, 0.35, 0.65, "SZ-C1", {"interface": "USB-UART", "package": "SOP-16"}),
    InventoryItem("SKU-IC-008", "MAX232 RS232 Driver", "interface_ic", 8000, 0.40, 0.75, "GZ-B2", {"interface": "RS232", "package": "DIP-16"}),
    InventoryItem("SKU-IC-009", "74HC595 Shift Register", "logic_ic", 20000, 0.10, 0.18, "SZ-A2", {"type": "shift_register", "bits": 8}),
    InventoryItem("SKU-IC-010", "TL431 Voltage Ref", "analog_ic", 40000, 0.05, 0.09, "SZ-A1", {"type": "voltage_reference"}),
    # ── 二极管 (Diodes) ──
    InventoryItem("SKU-DIO-001", "1N4148 Signal Diode", "diode", 100000, 0.005, 0.010, "SZ-A1", {"type": "signal", "package": "SOD-323"}),
    InventoryItem("SKU-DIO-002", "1N5819 Schottky", "diode", 50000, 0.015, 0.028, "SZ-A2", {"type": "schottky", "vf": "0.45V"}),
    InventoryItem("SKU-DIO-003", "SS34 Schottky SMD", "diode", 35000, 0.020, 0.038, "GZ-B1", {"type": "schottky", "current": "3A"}),
    InventoryItem("SKU-DIO-004", "LED Red 0805", "led", 200000, 0.003, 0.006, "SZ-A1", {"color": "red", "package": "0805"}),
    InventoryItem("SKU-DIO-005", "LED White 5mm", "led", 80000, 0.008, 0.015, "GZ-B1", {"color": "white", "package": "5mm"}),
    InventoryItem("SKU-DIO-006", "Zener 5.1V SOD-123", "diode", 60000, 0.008, 0.015, "SZ-A2", {"type": "zener", "voltage": "5.1V"}),
    # ── 晶体管 (Transistors) ──
    InventoryItem("SKU-TR-001", "2N2222A NPN TO-92", "transistor", 40000, 0.012, 0.022, "SZ-A1", {"type": "NPN", "package": "TO-92"}),
    InventoryItem("SKU-TR-002", "S8050 NPN SOT-23", "transistor", 80000, 0.008, 0.015, "SZ-A2", {"type": "NPN", "package": "SOT-23"}),
    InventoryItem("SKU-TR-003", "IRF540N MOSFET TO-220", "transistor", 10000, 0.25, 0.48, "GZ-B1", {"type": "N-MOSFET", "vds": "100V"}),
    InventoryItem("SKU-TR-004", "AO3400 N-MOSFET SOT-23", "transistor", 50000, 0.015, 0.028, "SZ-A1", {"type": "N-MOSFET", "package": "SOT-23"}),
    InventoryItem("SKU-TR-005", "TIP41C NPN TO-220", "transistor", 15000, 0.08, 0.15, "GZ-B2", {"type": "NPN", "power": "65W"}),
    # ── 连接器 (Connectors) ──
    InventoryItem("SKU-CON-001", "USB Type-C 16P SMD", "connector", 20000, 0.12, 0.22, "SZ-C1", {"type": "USB-C", "pins": 16}),
    InventoryItem("SKU-CON-002", "2.54mm Pin Header 40P", "connector", 50000, 0.03, 0.06, "SZ-A1", {"pitch": "2.54mm", "pins": 40}),
    InventoryItem("SKU-CON-003", "JST-XH 4P Connector", "connector", 30000, 0.05, 0.09, "SZ-A2", {"type": "JST-XH", "pins": 4}),
    InventoryItem("SKU-CON-004", "RJ45 Ethernet Jack", "connector", 12000, 0.18, 0.35, "GZ-B1", {"type": "RJ45", "shielded": True}),
    InventoryItem("SKU-CON-005", "3.5mm Audio Jack", "connector", 25000, 0.06, 0.11, "SZ-A2", {"type": "audio", "channels": "stereo"}),
    # ── 电感 (Inductors) ──
    InventoryItem("SKU-IND-001", "10uH Power Inductor", "inductor", 30000, 0.025, 0.048, "SZ-A1", {"inductance": "10uH", "type": "power"}),
    InventoryItem("SKU-IND-002", "100uH Shielded SMD", "inductor", 20000, 0.035, 0.065, "SZ-A2", {"inductance": "100uH", "shielded": True}),
    InventoryItem("SKU-IND-003", "4.7uH Ferrite Bead", "inductor", 60000, 0.008, 0.015, "SZ-A1", {"inductance": "4.7uH", "type": "ferrite"}),
    # ── 晶振 (Crystals) ──
    InventoryItem("SKU-XTL-001", "8MHz Crystal HC-49S", "crystal", 15000, 0.08, 0.15, "GZ-B2", {"frequency": "8MHz", "package": "HC-49S"}),
    InventoryItem("SKU-XTL-002", "16MHz Crystal SMD", "crystal", 20000, 0.06, 0.11, "SZ-A1", {"frequency": "16MHz", "package": "3225"}),
    InventoryItem("SKU-XTL-003", "32.768KHz RTC Crystal", "crystal", 25000, 0.04, 0.08, "SZ-A2", {"frequency": "32.768KHz", "type": "RTC"}),
    # ── 传感器 (Sensors) ──
    InventoryItem("SKU-SEN-001", "DHT22 Temp/Humidity", "sensor", 3000, 1.50, 2.85, "SZ-C1", {"type": "temperature_humidity", "accuracy": "±0.5°C"}),
    InventoryItem("SKU-SEN-002", "MPU6050 6-Axis IMU", "sensor", 5000, 0.80, 1.50, "SZ-C1", {"type": "IMU", "axes": 6}),
    InventoryItem("SKU-SEN-003", "BMP280 Barometer", "sensor", 4000, 0.60, 1.15, "GZ-B2", {"type": "barometer", "interface": "I2C/SPI"}),
    InventoryItem("SKU-SEN-004", "HC-SR04 Ultrasonic", "sensor", 6000, 0.45, 0.85, "SZ-A2", {"type": "ultrasonic", "range": "2-400cm"}),
]

assert len(_PRESET_INVENTORY) == 50, f"Expected 50 SKUs, got {len(_PRESET_INVENTORY)}"


class MockInventory:
    """本地模拟库存管理器

    支持按名称模糊查询 + 库存数量校验。
    线程安全（内部数据不可变，查询为只读）。
    """

    def __init__(self, items: list[InventoryItem] | None = None) -> None:
        self._items: list[InventoryItem] = list(items or _PRESET_INVENTORY)

    @property
    def size(self) -> int:
        return len(self._items)

    def query(
        self,
        sku_name: str,
        qty: int = 1,
        category: str | None = None,
    ) -> list[dict]:
        """按名称模糊匹配 + 库存充足性过滤

        Parameters
        ----------
        sku_name : str
            SKU 名称关键词（大小写不敏感）
        qty : int
            需求数量，仅返回库存 >= qty 的记录
        category : str | None
            可选品类过滤

        Returns
        -------
        list[dict]
            匹配的库存记录列表，按成本价升序排列
        """
        keyword = sku_name.lower().strip()
        results: list[InventoryItem] = []

        for item in self._items:
            # 名称模糊匹配
            name_match = keyword in item.sku_name.lower() or keyword in item.sku_id.lower()
            # 品类匹配
            cat_match = category is None or category.lower() in item.category.lower()
            # specs 值匹配
            spec_match = any(keyword in str(v).lower() for v in item.specs.values())

            if (name_match or spec_match) and cat_match and item.stock_qty >= qty:
                results.append(item)

        # 按成本价升序
        results.sort(key=lambda x: x.cost_price)

        return [
            {
                "sku_id": r.sku_id,
                "sku_name": r.sku_name,
                "category": r.category,
                "stock_qty": r.stock_qty,
                "cost_price": r.cost_price,
                "suggested_sell_price": r.suggested_sell_price,
                "location": r.location,
                "specs": dict(r.specs),
                "profit_margin_pct": round(
                    (r.suggested_sell_price - r.cost_price) / r.cost_price * 100, 1
                ),
                "is_un_certified": r.is_un_certified,
                "is_rcep_eligible": r.is_rcep_eligible,
            }
            for r in results
        ]

    def remove_sku(self, sku_id: str) -> bool:
        """移除指定 SKU（用于测试 fallback 场景）"""
        before = len(self._items)
        self._items = [i for i in self._items if i.sku_id != sku_id]
        return len(self._items) < before

    def add_item(self, item: InventoryItem) -> None:
        """添加 SKU（用于测试）"""
        self._items.append(item)


# ── 单例 ──
_inventory_instance: MockInventory | None = None


def get_mock_inventory() -> MockInventory:
    """获取全局 MockInventory 单例"""
    global _inventory_instance
    if _inventory_instance is None:
        _inventory_instance = MockInventory()
    return _inventory_instance
