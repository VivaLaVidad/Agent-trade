"""
modules.supply_chain.models — 供应链撮合引擎数据模型
────────────────────────────────────────────────────
表结构：Supplier / ProductSKU / DemandOrder / MatchResult / PurchaseOrder
复用 database.models 的 Base / _uuid_pk / _ts_created / _ts_updated / EncryptedString
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models import Base, EncryptedString, _uuid_pk, _ts_created, _ts_updated


class Supplier(Base):
    """供应商主表（多租户隔离：merchant_id）"""
    __tablename__ = "suppliers"

    id: Mapped[str] = _uuid_pk()
    merchant_id: Mapped[str] = mapped_column(
        String(36), default="default", index=True, comment="所属商户ID（租户隔离键）",
    )
    name: Mapped[str] = mapped_column(String(256), comment="供应商名称")
    region: Mapped[str] = mapped_column(String(100), comment="所在产业带/地区")
    contact: Mapped[Optional[str]] = mapped_column(
        EncryptedString(), nullable=True, comment="联系方式（AES 加密）",
    )
    certifications: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None, comment="资质认证列表 ['CE','RoHS',...]",
    )
    rating: Mapped[float] = mapped_column(Float, default=4.0, comment="评分 1-5")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    skus: Mapped[list[ProductSKU]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan",
    )


class ProductSKU(Base):
    """工业品 SKU 目录"""
    __tablename__ = "product_skus"
    __table_args__ = (
        Index("ix_sku_category_brand", "category", "brand"),
    )

    id: Mapped[str] = _uuid_pk()
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    category: Mapped[str] = mapped_column(String(60), comment="品类: capacitor/resistor/ic/led/connector/pcb")
    name: Mapped[str] = mapped_column(String(256), comment="SKU 名称/型号")
    brand: Mapped[str] = mapped_column(String(100), default="", comment="品牌")
    specs: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None,
        comment="规格参数 {voltage,current,package,tolerance,...}",
    )
    unit_price_rmb: Mapped[float] = mapped_column(Float, comment="单价(人民币)")
    moq: Mapped[int] = mapped_column(Integer, default=100, comment="最低起订量")
    stock_qty: Mapped[int] = mapped_column(Integer, default=0, comment="现货库存")
    certifications: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None, comment="该 SKU 持有的认证",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts_created()

    supplier: Mapped[Supplier] = relationship(back_populates="skus")


class DemandOrder(Base):
    """C端采购需求单（多租户隔离：merchant_id + client_id）"""
    __tablename__ = "demand_orders"
    __table_args__ = (
        Index("ix_demand_status", "status"),
        Index("ix_demand_merchant", "merchant_id"),
    )

    id: Mapped[str] = _uuid_pk()
    merchant_id: Mapped[str] = mapped_column(
        String(36), default="default", comment="撮合商户ID（租户隔离键）",
    )
    client_id: Mapped[str] = mapped_column(
        String(36), default="", comment="C端客户唯一标识",
    )
    buyer_name: Mapped[str] = mapped_column(String(128), comment="采购方名称")
    buyer_country: Mapped[str] = mapped_column(String(60), comment="采购方国家")
    product_keywords: Mapped[str] = mapped_column(String(512), comment="产品关键词")
    specs_required: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None, comment="需求规格参数",
    )
    quantity: Mapped[int] = mapped_column(Integer, comment="需求数量")
    budget_usd: Mapped[float] = mapped_column(Float, default=0.0, comment="预算(美元)")
    certs_required: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None, comment="要求的认证 ['CE','RoHS']",
    )
    destination: Mapped[str] = mapped_column(String(128), default="", comment="目的地")
    urgency: Mapped[str] = mapped_column(String(20), default="medium", comment="紧急度")
    status: Mapped[str] = mapped_column(
        String(20), default="pending",
        comment="pending/matched/completed/failed",
    )
    raw_input: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="原始询盘文本")
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class MatchResult(Base):
    """撮合结果记录"""
    __tablename__ = "match_results"
    __table_args__ = (
        Index("ix_match_demand", "demand_id"),
    )

    id: Mapped[str] = _uuid_pk()
    demand_id: Mapped[str] = mapped_column(ForeignKey("demand_orders.id"))
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"))
    sku_id: Mapped[str] = mapped_column(ForeignKey("product_skus.id"))
    match_score: Mapped[float] = mapped_column(Float, default=0.0, comment="撮合评分 0-1")
    quoted_price_usd: Mapped[float] = mapped_column(Float, default=0.0, comment="报价(美元)")
    shipping_term: Mapped[str] = mapped_column(String(10), default="FOB", comment="贸易术语")
    negotiation_notes: Mapped[Optional[str]] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(
        String(20), default="proposed",
        comment="proposed/approved/rejected",
    )
    created_at: Mapped[datetime] = _ts_created()


class PurchaseOrder(Base):
    """AI 生成的采购订单"""
    __tablename__ = "purchase_orders"

    id: Mapped[str] = _uuid_pk()
    match_id: Mapped[str] = mapped_column(ForeignKey("match_results.id"))
    po_number: Mapped[str] = mapped_column(String(32), unique=True, comment="订单编号")
    items_json: Mapped[Optional[dict]] = mapped_column(JSON, default=None, comment="订单明细")
    total_rmb: Mapped[float] = mapped_column(Float, default=0.0)
    total_usd: Mapped[float] = mapped_column(Float, default=0.0)
    fx_rate: Mapped[float] = mapped_column(Float, default=7.25, comment="成交汇率")
    shipping_term: Mapped[str] = mapped_column(String(10), default="FOB")
    payment_term: Mapped[str] = mapped_column(String(60), default="T/T 30% deposit")
    content: Mapped[Optional[str]] = mapped_column(Text, default=None, comment="订单正文")
    is_encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts_created()


class TransactionLedger(Base):
    """交易账本 —— 每笔撮合成交自动生成流水 + 路由费 + SHA256 签名 + Ticker ID"""
    __tablename__ = "transaction_ledger"
    __table_args__ = (
        Index("ix_ledger_merchant", "merchant_id"),
        Index("ix_ledger_created", "created_at"),
        Index("ix_ledger_ticker", "ticker_id"),
    )

    id: Mapped[str] = _uuid_pk()
    transaction_id: Mapped[str] = mapped_column(
        String(36), unique=True, comment="交易流水号(UUID)",
    )
    ticker_id: Mapped[str] = mapped_column(
        String(64), default="", comment="标准化 Ticker ID (CLAW-ELEC-CAP-100NF50V)",
    )
    merchant_id: Mapped[str] = mapped_column(String(36), comment="撮合商户ID")
    client_id: Mapped[str] = mapped_column(String(36), default="", comment="C端客户ID")
    match_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("match_results.id"), nullable=True,
    )
    po_number: Mapped[str] = mapped_column(String(32), default="", comment="关联PO编号")
    amount_usd: Mapped[float] = mapped_column(Float, comment="成交金额(USD)")
    routing_fee_usd: Mapped[float] = mapped_column(Float, comment="路由费(USD) = amount * 1%")
    fee_rate: Mapped[float] = mapped_column(Float, default=0.01, comment="费率")
    signature: Mapped[str] = mapped_column(
        String(64), comment="SHA256 数字签名（防篡改，含 Ticker ID）",
    )
    status: Mapped[str] = mapped_column(
        String(20), default="settled", comment="settled/pending/reversed",
    )
    created_at: Mapped[datetime] = _ts_created()


class NegotiationRound(Base):
    """谈判轮次记录 —— 多轮报价/还价状态追踪"""
    __tablename__ = "negotiation_rounds"
    __table_args__ = (
        Index("ix_neg_negotiation", "negotiation_id"),
        Index("ix_neg_match", "match_id"),
        Index("ix_neg_merchant", "merchant_id"),
    )

    id: Mapped[str] = _uuid_pk()
    negotiation_id: Mapped[str] = mapped_column(
        String(36), comment="谈判会话ID（同一撮合的多轮共享）",
    )
    match_id: Mapped[str] = mapped_column(String(36), default="", comment="关联撮合ID")
    demand_id: Mapped[str] = mapped_column(String(36), default="", comment="关联需求ID")
    merchant_id: Mapped[str] = mapped_column(String(36), default="", comment="商户ID")
    client_id: Mapped[str] = mapped_column(String(36), default="", comment="C端客户ID")
    round_number: Mapped[int] = mapped_column(Integer, default=1, comment="轮次序号")
    status: Mapped[str] = mapped_column(
        String(20), default="pending",
        comment="pending/counter_offer/accepted/rejected/expired",
    )
    action: Mapped[str] = mapped_column(
        String(30), default="",
        comment="seller_quote/buyer_accept/buyer_counter/buyer_reject",
    )
    seller_offer: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None, comment="卖家报价 {unit_price_usd, quantity, shipping_term, ...}",
    )
    buyer_offer: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None, comment="买家还价 {unit_price_usd, quantity, ...}",
    )
    delta_highlight: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None,
        comment="差值高亮 {old_price, new_price, delta_usd, delta_pct, direction}",
    )
    created_at: Mapped[datetime] = _ts_created()


class UpstreamSupplier(Base):
    """上游供应商 —— Buy-side 采购对冲的供应商池"""
    __tablename__ = "upstream_suppliers"

    id: Mapped[str] = _uuid_pk()
    supplier_name: Mapped[str] = mapped_column(String(256), comment="上游供应商名称")
    region: Mapped[str] = mapped_column(String(100), default="", comment="所在地区")
    credibility_score: Mapped[float] = mapped_column(
        Float, default=80.0, comment="信用评分 0-100",
    )
    api_endpoint: Mapped[str] = mapped_column(
        String(512), default="", comment="询价 API 端点 (mock 或真实)",
    )
    specialties: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None, comment="擅长品类 ['capacitor','resistor',...]",
    )
    min_order_usd: Mapped[float] = mapped_column(Float, default=50.0, comment="最低采购额(USD)")
    avg_lead_days: Mapped[int] = mapped_column(Integer, default=7, comment="平均交期(天)")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class ProcurementOrder(Base):
    """采购锁单 —— Buy-side 背靠背套利的上游 PO"""
    __tablename__ = "procurement_orders"
    __table_args__ = (
        Index("ix_procurement_trade", "matched_trade_id"),
        Index("ix_procurement_supplier", "supplier_id"),
    )

    id: Mapped[str] = _uuid_pk()
    po_hash: Mapped[str] = mapped_column(
        String(64), unique=True, comment="SHA-256 采购单哈希 (防篡改)",
    )
    matched_trade_id: Mapped[str] = mapped_column(
        String(36), comment="关联的 Sell-side 交易流水号",
    )
    supplier_id: Mapped[str] = mapped_column(
        String(36), comment="上游供应商 ID",
    )
    supplier_name: Mapped[str] = mapped_column(String(256), default="", comment="上游供应商名称")
    ticker_id: Mapped[str] = mapped_column(String(64), default="", comment="标准化 Ticker ID")
    cost_price_usd: Mapped[float] = mapped_column(Float, comment="采购成本价(USD)")
    sell_price_usd: Mapped[float] = mapped_column(Float, default=0.0, comment="对外售价(USD)")
    quantity: Mapped[int] = mapped_column(Integer, default=0, comment="采购数量")
    shipping_estimate_usd: Mapped[float] = mapped_column(Float, default=0.0, comment="预估运费(USD)")
    arbitrage_spread_usd: Mapped[float] = mapped_column(
        Float, default=0.0, comment="套利差 = sell - cost - shipping",
    )
    arbitrage_pct: Mapped[float] = mapped_column(Float, default=0.0, comment="套利率(%)")
    lock_status: Mapped[str] = mapped_column(
        String(20), default="pending",
        comment="pending / locked / failed / cancelled",
    )
    document_hash: Mapped[str] = mapped_column(String(64), default="", comment="PO PDF SHA-256")
    created_at: Mapped[datetime] = _ts_created()
