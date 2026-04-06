"""
modules.supply_chain.mock_data — 电子元器件模拟数据生成器
──────────────────────────────────────────────────────
生成 50 家供应商 + 200 个 SKU，涵盖电容/电阻/IC/LED/连接器/PCB，
包含真实规格参数、价格区间、MOQ、认证信息。
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

_REGIONS = [
    "深圳华强北", "深圳南山", "东莞长安", "东莞虎门", "苏州工业园",
    "苏州昆山", "上海浦东", "杭州余杭", "宁波北仑", "厦门海沧",
]

_SUPPLIER_PREFIXES = [
    "华强", "鼎芯", "瑞科", "中电", "恒达", "盛源", "创联", "信达", "宏微", "博科",
    "合泰", "天成", "精芯", "永泰", "华创", "德普", "科达", "新锐", "联发", "明微",
    "优芯", "力芯", "正邦", "金昌", "威科", "飞利", "诚信", "硅力", "芯联", "海纳",
    "普瑞", "拓微", "新华", "百通", "元亨", "长信", "兆易", "紫光", "凯虹", "国芯",
    "智微", "锐迪", "富满", "明阳", "赛微", "汇顶", "卓胜", "恒玄", "芯海", "乐鑫",
]

_SUFFIX = ["电子", "科技", "半导体", "元器件", "微电子"]

_CERTS_POOL = ["CE", "RoHS", "UL", "FCC", "ISO9001", "ISO14001", "REACH", "AEC-Q100"]

# 与 run_business._DEMO_REQUESTS 等演示脚本一致，否则按 tenant 过滤会 0 命中
_MOCK_MERCHANT_IDS = ("merchant-alpha-001", "merchant-beta-002")

_CATEGORIES: dict[str, list[dict[str, Any]]] = {
    "capacitor": [
        {"name": "MLCC 贴片电容 {cap} {volt}", "brand_pool": ["Murata", "Samsung", "Yageo", "TDK", "CCTC"],
         "specs_gen": lambda: {"capacitance": random.choice(["100pF","1nF","100nF","1uF","10uF","100uF","470uF"]),
                               "voltage": random.choice(["10V","16V","25V","50V","100V"]),
                               "package": random.choice(["SMD-0201","SMD-0402","SMD-0603","SMD-0805","SMD-1206"]),
                               "tolerance": random.choice(["5%","10%","20%"]),
                               "dielectric": random.choice(["C0G","X5R","X7R","Y5V"])},
         "price_range": (0.005, 0.8), "moq_range": (1000, 50000)},
    ],
    "resistor": [
        {"name": "贴片电阻 {res} {pkg}", "brand_pool": ["Yageo", "UniOhm", "Panasonic", "Vishay", "FH"],
         "specs_gen": lambda: {"resistance": random.choice(["10R","100R","1K","4.7K","10K","100K","1M"]),
                               "power": random.choice(["1/16W","1/8W","1/4W","1/2W"]),
                               "package": random.choice(["SMD-0402","SMD-0603","SMD-0805","SMD-1206","SMD-2512"]),
                               "tolerance": random.choice(["1%","5%"])},
         "price_range": (0.002, 0.1), "moq_range": (5000, 100000)},
    ],
    "ic": [
        {"name": "{brand} {type} 芯片", "brand_pool": ["TI", "STM", "NXP", "Microchip", "Infineon", "Espressif", "GigaDevice"],
         "specs_gen": lambda: {"type": random.choice(["MCU","OpAmp","LDO","DC-DC","ADC","DAC","MOSFET","Gate Driver"]),
                               "package": random.choice(["QFP-44","QFP-64","QFN-24","SOIC-8","SOIC-16","TSSOP-20","BGA-256"]),
                               "voltage_range": random.choice(["1.8-3.6V","3-5.5V","5-36V","2.7-5V"]),
                               "frequency": random.choice(["8MHz","48MHz","72MHz","168MHz","240MHz","N/A"])},
         "price_range": (0.5, 45.0), "moq_range": (100, 5000)},
    ],
    "led": [
        {"name": "{color} LED {pkg}", "brand_pool": ["Cree", "Osram", "Lumileds", "Everlight", "Nationstar"],
         "specs_gen": lambda: {"color": random.choice(["White","Red","Green","Blue","Yellow","RGB"]),
                               "wavelength": random.choice(["460nm","520nm","590nm","620nm","6500K","3000K","N/A"]),
                               "package": random.choice(["SMD-2835","SMD-5050","SMD-3528","DIP-5mm","COB"]),
                               "luminous_flux": random.choice(["20lm","50lm","100lm","200lm"])},
         "price_range": (0.02, 2.0), "moq_range": (500, 20000)},
    ],
    "connector": [
        {"name": "{type} 连接器 {pins}Pin", "brand_pool": ["Molex", "TE","JST","Amphenol","HRS"],
         "specs_gen": lambda: {"type": random.choice(["USB-C","Micro-USB","FPC","Board-to-Board","Pin Header","RJ45","DC Jack"]),
                               "pins": random.choice([4,6,8,10,20,24,40]),
                               "pitch": random.choice(["0.5mm","1.0mm","1.27mm","2.0mm","2.54mm"]),
                               "current_rating": random.choice(["0.5A","1A","2A","3A","5A"])},
         "price_range": (0.1, 8.0), "moq_range": (200, 10000)},
    ],
    "pcb": [
        {"name": "{layer}层 PCB 板 {material}", "brand_pool": ["JLC","ALLPCB","PCBWay","WellPCB","RushPCB"],
         "specs_gen": lambda: {"layers": random.choice([1,2,4,6,8]),
                               "material": random.choice(["FR-4","Aluminum","Rogers","CEM-3"]),
                               "thickness": random.choice(["0.8mm","1.0mm","1.2mm","1.6mm","2.0mm"]),
                               "min_trace": random.choice(["3mil","4mil","5mil","6mil"]),
                               "surface_finish": random.choice(["HASL","ENIG","OSP","Immersion Tin"])},
         "price_range": (2.0, 50.0), "moq_range": (5, 500)},
    ],
}


def _make_supplier(idx: int) -> dict[str, Any]:
    name = f"{_SUPPLIER_PREFIXES[idx % len(_SUPPLIER_PREFIXES)]}{random.choice(_SUFFIX)}"
    region = random.choice(_REGIONS)
    num_certs = random.randint(2, 5)
    certs = random.sample(_CERTS_POOL, min(num_certs, len(_CERTS_POOL)))
    merchant_id = _MOCK_MERCHANT_IDS[idx % len(_MOCK_MERCHANT_IDS)]
    return {
        "id": str(uuid.uuid4()),
        "merchant_id": merchant_id,
        "name": name,
        "region": region,
        "certifications": certs,
        "rating": round(random.uniform(3.0, 5.0), 1),
        "contact": f"+86-{random.randint(130,199)}-{random.randint(1000,9999)}-{random.randint(1000,9999)}",
    }


def _make_sku(supplier_id: str, category: str, template: dict) -> dict[str, Any]:
    specs = template["specs_gen"]()
    brand = random.choice(template["brand_pool"])
    lo, hi = template["price_range"]
    moq_lo, moq_hi = template["moq_range"]

    name_vars = {**specs, "brand": brand, "cap": specs.get("capacitance",""),
                 "volt": specs.get("voltage",""), "res": specs.get("resistance",""),
                 "pkg": specs.get("package",""), "color": specs.get("color",""),
                 "type": specs.get("type",""), "pins": str(specs.get("pins","")),
                 "layer": str(specs.get("layers","")), "material": specs.get("material","")}
    try:
        name = template["name"].format(**name_vars)
    except (KeyError, IndexError):
        name = f"{brand} {category}"

    sku_certs = random.sample(["CE","RoHS","UL","FCC"], random.randint(0, 3))

    return {
        "id": str(uuid.uuid4()),
        "supplier_id": supplier_id,
        "category": category,
        "name": name,
        "brand": brand,
        "specs": specs,
        "unit_price_rmb": round(random.uniform(lo, hi), 4),
        "moq": random.randint(moq_lo, moq_hi),
        "stock_qty": random.randint(0, 500000),
        "certifications": sku_certs,
    }


async def generate_mock_catalog(
    num_suppliers: int = 50,
    num_skus: int = 200,
) -> dict[str, int]:
    """生成模拟供应商目录并写入数据库

    Returns
    -------
    dict
        {"suppliers": int, "skus": int}
    """
    from database.models import AsyncSessionFactory
    from modules.supply_chain.models import Supplier, ProductSKU

    suppliers_data = [_make_supplier(i) for i in range(num_suppliers)]
    supplier_ids = [s["id"] for s in suppliers_data]

    categories = list(_CATEGORIES.keys())
    skus_data: list[dict] = []
    for _ in range(num_skus):
        cat = random.choice(categories)
        template = random.choice(_CATEGORIES[cat])
        sid = random.choice(supplier_ids)
        skus_data.append(_make_sku(sid, cat, template))

    async with AsyncSessionFactory() as session:
        for sd in suppliers_data:
            session.add(Supplier(
                id=sd["id"], merchant_id=sd["merchant_id"], name=sd["name"],
                region=sd["region"], contact=sd["contact"],
                certifications=sd["certifications"], rating=sd["rating"],
            ))
        await session.flush()

        for skd in skus_data:
            session.add(ProductSKU(
                id=skd["id"], supplier_id=skd["supplier_id"],
                category=skd["category"], name=skd["name"], brand=skd["brand"],
                specs=skd["specs"], unit_price_rmb=skd["unit_price_rmb"],
                moq=skd["moq"], stock_qty=skd["stock_qty"],
                certifications=skd["certifications"],
            ))
        await session.commit()

    logger.info("模拟数据已生成: %d 供应商, %d SKU", num_suppliers, num_skus)
    return {"suppliers": num_suppliers, "skus": num_skus}
