"""
database.models — SQLAlchemy 2.0 异步数据模型
──────────────────────────────────────────────
职责边界：
  1. 定义所有业务数据表（会话、意图、策略、RPA日志）
  2. 提供异步 Engine / Session 工厂
  3. 表结构迁移入口 (create_tables)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Optional

from pydantic_settings import BaseSettings
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    func,
)
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)

from core.security import get_cipher


# ─── Configuration ───────────────────────────────────────────
class DBSettings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/omniedge"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


_db_settings = DBSettings()

_engine_kwargs: dict = {"echo": False}
if "sqlite" not in _db_settings.DATABASE_URL:
    _engine_kwargs.update(pool_size=10, max_overflow=20, pool_pre_ping=True)

async_engine = create_async_engine(_db_settings.DATABASE_URL, **_engine_kwargs)

AsyncSessionFactory = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ═════════════════════════════════════════════════════════════
#  Base
# ═════════════════════════════════════════════════════════════
class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[str]:
    return mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )


def _ts_created() -> Mapped[datetime]:
    return mapped_column(DateTime, server_default=func.now())


def _ts_updated() -> Mapped[datetime]:
    return mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# ═════════════════════════════════════════════════════════════
#  透明加解密列类型
# ═════════════════════════════════════════════════════════════
class EncryptedString(TypeDecorator):
    """SQLAlchemy 自定义列类型 —— AES-256-GCM 透明加解密

    底层存储为 ``LargeBinary``，应用层交互为 ``str``。
    写入时自动将明文 str 经 AES-256-GCM 加密为 bytes；
    读取时自动将 bytes 解密还原为 str。

    密码器通过 ``core.security.get_cipher()`` 懒加载获取，
    仅在首次实际读写数据库时才初始化密钥。

    存储格式: nonce(12B) || ciphertext || GCM-tag(16B)
    """

    impl = LargeBinary
    cache_ok: bool = True

    def process_bind_param(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> bytes | None:
        """ORM 写入拦截 —— 明文 str → AES-256-GCM 密文 bytes

        Parameters
        ----------
        value : str | None
            应用层传入的明文字符串，None 时直接透传
        dialect : Dialect
            当前数据库方言（由 SQLAlchemy 自动注入）

        Returns
        -------
        bytes | None
            加密后的密文字节串，或 None
        """
        if value is not None:
            return get_cipher().encrypt_string(value)
        return None

    def process_result_value(
        self,
        value: bytes | None,
        dialect: Dialect,
    ) -> str | None:
        """ORM 读取拦截 —— AES-256-GCM 密文 bytes → 明文 str

        Parameters
        ----------
        value : bytes | None
            数据库中存储的密文字节串，None 时直接透传
        dialect : Dialect
            当前数据库方言（由 SQLAlchemy 自动注入）

        Returns
        -------
        str | None
            解密后的明文字符串，或 None
        """
        if value is not None:
            return get_cipher().decrypt_string(value)
        return None


# ═════════════════════════════════════════════════════════════
#  Tables
# ═════════════════════════════════════════════════════════════

class TradeSession(Base):
    """会话主表 — 一次完整的客户交互周期"""
    __tablename__ = "trade_sessions"

    id: Mapped[str] = _uuid_pk()
    external_session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    context: Mapped[Optional[dict]] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    intents: Mapped[list[IntentRecord]] = relationship(back_populates="session", cascade="all, delete-orphan")
    strategies: Mapped[list[StrategyRecord]] = relationship(back_populates="session", cascade="all, delete-orphan")
    rpa_logs: Mapped[list[RPALog]] = relationship(back_populates="session", cascade="all, delete-orphan")


class IntentRecord(Base):
    """意图分析记录"""
    __tablename__ = "intent_records"
    __table_args__ = (
        Index("ix_intent_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = _uuid_pk()
    session_id: Mapped[str] = mapped_column(ForeignKey("trade_sessions.id"))
    category: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float)
    sentiment: Mapped[str] = mapped_column(String(20))
    urgency: Mapped[str] = mapped_column(String(20))
    summary: Mapped[str] = mapped_column(Text)
    entities: Mapped[Optional[dict]] = mapped_column(JSON, default=None)
    raw_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts_created()

    session: Mapped[TradeSession] = relationship(back_populates="intents")


class StrategyRecord(Base):
    """策略决策记录"""
    __tablename__ = "strategy_records"
    __table_args__ = (
        Index("ix_strategy_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = _uuid_pk()
    session_id: Mapped[str] = mapped_column(ForeignKey("trade_sessions.id"))
    risk_level: Mapped[str] = mapped_column(String(20))
    actions: Mapped[dict] = mapped_column(JSON)
    reply_draft: Mapped[str] = mapped_column(Text)
    requires_human: Mapped[bool] = mapped_column(Boolean, default=False)
    knowledge_refs: Mapped[Optional[list]] = mapped_column(JSON, default=None)
    notes: Mapped[Optional[str]] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = _ts_created()

    session: Mapped[TradeSession] = relationship(back_populates="strategies")


class RPALog(Base):
    """RPA 执行日志"""
    __tablename__ = "rpa_logs"
    __table_args__ = (
        Index("ix_rpa_session_status", "session_id", "status"),
    )

    id: Mapped[str] = _uuid_pk()
    session_id: Mapped[str] = mapped_column(ForeignKey("trade_sessions.id"))
    task_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20))
    params: Mapped[Optional[dict]] = mapped_column(JSON, default=None)
    result: Mapped[Optional[dict]] = mapped_column(JSON, default=None)
    error_message: Mapped[Optional[str]] = mapped_column(Text, default=None)
    duration_ms: Mapped[Optional[int]] = mapped_column(default=None)
    created_at: Mapped[datetime] = _ts_created()

    session: Mapped[TradeSession] = relationship(back_populates="rpa_logs")


class KnowledgeDocument(Base):
    """知识库文档元数据"""
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str] = mapped_column(String(30))
    meta_data: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class ClientLead(Base):
    """客户线索表 —— 敏感字段采用 AES-256-GCM 透明加解密

    加密策略
    --------
    ``client_email`` 与 ``contact_info`` 在 ORM 层通过 :class:`EncryptedString`
    自动调用 ``core.security.AESCipher`` 进行加密写入与解密读取。
    数据库列类型为 ``LargeBinary``，存储 nonce(12B) || ciphertext || tag(16B)。

    Attributes
    ----------
    id : str
        UUID v4 主键
    client_name : str
        客户姓名（明文）
    client_email : str | None
        客户邮箱（AES-256-GCM 加密存储）
    contact_info : str | None
        联系方式（AES-256-GCM 加密存储，可为手机号/微信/QQ 等）
    company : str | None
        客户所属公司
    source : str
        线索来源渠道（如：官网、展会、转介绍）
    status : str
        线索状态（new / contacted / qualified / converted / closed）
    priority : str
        优先级（low / medium / high / urgent）
    tags : dict | None
        自定义标签 JSON
    notes : str | None
        跟进备注（明文）
    is_active : bool
        软删除标志
    created_at : datetime
        创建时间（数据库自动填充）
    updated_at : datetime
        最后更新时间（数据库自动填充）
    """

    __tablename__ = "client_leads"
    __table_args__ = (
        Index("ix_lead_source_status", "source", "status"),
        Index("ix_lead_created", "created_at"),
    )

    id: Mapped[str] = _uuid_pk()
    client_name: Mapped[str] = mapped_column(
        String(128), comment="客户姓名",
    )
    client_email: Mapped[Optional[str]] = mapped_column(
        EncryptedString(), nullable=True, comment="客户邮箱（AES-256-GCM 加密存储）",
    )
    contact_info: Mapped[Optional[str]] = mapped_column(
        EncryptedString(), nullable=True, comment="联系方式（AES-256-GCM 加密存储）",
    )
    company: Mapped[Optional[str]] = mapped_column(
        String(256), nullable=True, comment="客户所属公司",
    )
    source: Mapped[str] = mapped_column(
        String(60), comment="线索来源渠道",
    )
    status: Mapped[str] = mapped_column(
        String(20), default="new", comment="线索状态",
    )
    priority: Mapped[str] = mapped_column(
        String(10), default="medium", comment="优先级",
    )
    tags: Mapped[Optional[dict]] = mapped_column(
        JSON, default=None, comment="自定义标签",
    )
    notes: Mapped[Optional[str]] = mapped_column(
        Text, default=None, comment="跟进备注",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="软删除标志",
    )
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    def __repr__(self) -> str:
        return (
            f"<ClientLead id={self.id[:8]}… "
            f"name={self.client_name!r} status={self.status!r}>"
        )


class EmailCampaign(Base):
    """邮件营销活动记录"""
    __tablename__ = "email_campaigns"

    id: Mapped[str] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(256), comment="活动名称")
    total_generated: Mapped[int] = mapped_column(default=0, comment="生成邮件数")
    total_sent: Mapped[int] = mapped_column(default=0, comment="已发送数")
    total_replied: Mapped[int] = mapped_column(default=0, comment="已回复数")
    status: Mapped[str] = mapped_column(String(20), default="draft", comment="活动状态")
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    messages: Mapped[list[CampaignMessage]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan",
    )


class CampaignMessage(Base):
    """营销邮件明细"""
    __tablename__ = "campaign_messages"
    __table_args__ = (
        Index("ix_cm_campaign_status", "campaign_id", "status"),
    )

    id: Mapped[str] = _uuid_pk()
    campaign_id: Mapped[str] = mapped_column(ForeignKey("email_campaigns.id"))
    recipient_email: Mapped[Optional[str]] = mapped_column(
        EncryptedString(), nullable=True, comment="收件人邮箱（AES 加密）",
    )
    subject: Mapped[str] = mapped_column(String(512), comment="邮件主题")
    body: Mapped[str] = mapped_column(Text, comment="邮件正文")
    stage: Mapped[str] = mapped_column(String(20), default="intro", comment="序列阶段")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="发送状态")
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = _ts_created()

    campaign: Mapped[EmailCampaign] = relationship(back_populates="messages")


class IdempotencyKey(Base):
    """金融幂等键 —— 跨进程去重（与 core.security.IdempotencyGuard 配套）"""

    __tablename__ = "idempotency_keys"
    __table_args__ = (Index("ix_idempotency_expires_at", "expires_at"),)

    trade_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class GeneratedDocument(Base):
    """AI 生成的商业文档（报价单/合同/发票）"""
    __tablename__ = "generated_documents"

    id: Mapped[str] = _uuid_pk()
    doc_type: Mapped[str] = mapped_column(
        String(30), comment="文档类型: quotation / proforma_invoice / contract",
    )
    client_name: Mapped[str] = mapped_column(String(128), default="", comment="关联客户名")
    content: Mapped[str] = mapped_column(Text, comment="文档内容（可能已加密）")
    is_encrypted: Mapped[bool] = mapped_column(Boolean, default=True, comment="内容是否 AES 加密")
    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


# ─── Table Creation ──────────────────────────────────────────
async def create_tables() -> None:
    # 侧效导入：将定义于独立模块的 ORM 注册进 Base.metadata（须早于 create_all）
    import core.long_term_memory  # noqa: F401 — opponent_profiles
    import modules.documents.invoice_generator  # noqa: F401 — document_hashes
    import modules.supply_chain.models  # noqa: F401 — upstream_suppliers / procurement_orders 等

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
