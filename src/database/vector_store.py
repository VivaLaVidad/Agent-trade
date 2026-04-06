"""
database.vector_store — pgvector 私有知识库 (RAG)
──────────────────────────────────────────────────
职责边界：
  1. 管理 pgvector 向量表（文档 embedding 存储）
  2. 统一 embedding 生成接口（支持 OpenAI / 本地模型切换）
  3. 语义相似度检索
  4. 文档的增删改查 & 批量入库
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
from langchain_openai import OpenAIEmbeddings
from pgvector.sqlalchemy import Vector
from pydantic_settings import BaseSettings
from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    String,
    Text,
    delete,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from database.models import AsyncSessionFactory, Base, _ts_created, _ts_updated, _uuid_pk
from core.logger import get_logger

logger = get_logger(__name__)

_EMBEDDING_DIM = 1536


# ─── Configuration ───────────────────────────────────────────
class VectorStoreSettings(BaseSettings):
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = _EMBEDDING_DIM
    DEFAULT_TOP_K: int = 5
    SIMILARITY_THRESHOLD: float = 0.7

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# ═════════════════════════════════════════════════════════════
#  Vector Table
# ═════════════════════════════════════════════════════════════
class DocumentEmbedding(Base):
    """向量化文档存储表"""
    __tablename__ = "document_embeddings"
    __table_args__ = (
        Index(
            "ix_doc_embedding_cosine",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_with={"lists": 100},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[str] = _uuid_pk()
    doc_id: Mapped[str] = mapped_column(String(36), index=True)
    chunk_index: Mapped[int] = mapped_column(default=0)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(_EMBEDDING_DIM))
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[Any] = _ts_created()


# ═════════════════════════════════════════════════════════════
#  Embedding Provider
# ═════════════════════════════════════════════════════════════
class EmbeddingProvider:
    """统一 embedding 接口，可扩展本地模型"""

    def __init__(self, settings: Optional[VectorStoreSettings] = None):
        self._settings = settings or VectorStoreSettings()
        self._embeddings = OpenAIEmbeddings(
            api_key=self._settings.OPENAI_API_KEY,
            base_url=self._settings.OPENAI_BASE_URL,
            model=self._settings.EMBEDDING_MODEL,
            dimensions=self._settings.EMBEDDING_DIM,
        )

    async def embed_text(self, text: str) -> list[float]:
        return await self._embeddings.aembed_query(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return await self._embeddings.aembed_documents(texts)


# ═════════════════════════════════════════════════════════════
#  Vector Store Manager
# ═════════════════════════════════════════════════════════════
class VectorStoreManager:
    """知识库向量存储管理器"""

    def __init__(self, settings: Optional[VectorStoreSettings] = None):
        self._settings = settings or VectorStoreSettings()
        self._embedder = EmbeddingProvider(self._settings)

    # ─── 写入 ────────────────────────────────────────────────
    async def add_document(
        self,
        doc_id: str,
        chunks: list[str],
        metadata: Optional[dict] = None,
    ) -> int:
        """
        将文档分块后向量化存入
        :param doc_id: 关联的文档 ID
        :param chunks: 文档分块文本列表
        :param metadata: 附加元数据
        :returns: 入库的向量条数
        """
        logger.info("开始入库文档: doc_id=%s chunks=%d", doc_id, len(chunks))

        embeddings = await self._embedder.embed_batch(chunks)

        async with AsyncSessionFactory() as session:
            for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                record = DocumentEmbedding(
                    doc_id=doc_id,
                    chunk_index=idx,
                    content=chunk,
                    embedding=emb,
                    metadata_=metadata,
                )
                session.add(record)
            await session.commit()

        logger.info("文档入库完成: doc_id=%s vectors=%d", doc_id, len(chunks))
        return len(chunks)

    # ─── 检索 ────────────────────────────────────────────────
    async def similarity_search(
        self,
        query: str,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        filter_doc_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        语义相似度检索
        :param query: 查询文本
        :param top_k: 返回条数
        :param threshold: 相似度阈值
        :param filter_doc_id: 限定文档范围
        """
        k = top_k or self._settings.DEFAULT_TOP_K
        thresh = threshold or self._settings.SIMILARITY_THRESHOLD

        query_embedding = await self._embedder.embed_text(query)

        async with AsyncSessionFactory() as session:
            distance_expr = DocumentEmbedding.embedding.cosine_distance(query_embedding)

            stmt = (
                select(
                    DocumentEmbedding.id,
                    DocumentEmbedding.doc_id,
                    DocumentEmbedding.chunk_index,
                    DocumentEmbedding.content,
                    DocumentEmbedding.metadata_,
                    (1 - distance_expr).label("similarity"),
                )
                .where(DocumentEmbedding.is_active.is_(True))
                .order_by(distance_expr)
                .limit(k)
            )

            if filter_doc_id:
                stmt = stmt.where(DocumentEmbedding.doc_id == filter_doc_id)

            result = await session.execute(stmt)
            rows = result.all()

        docs = []
        for row in rows:
            sim = float(row.similarity)
            if sim < thresh:
                continue
            docs.append({
                "id": row.id,
                "doc_id": row.doc_id,
                "chunk_index": row.chunk_index,
                "content": row.content,
                "metadata": row.metadata_,
                "similarity": round(sim, 4),
            })

        logger.info("向量检索完成: query_len=%d results=%d", len(query), len(docs))
        return docs

    # ─── 删除 ────────────────────────────────────────────────
    async def delete_document(self, doc_id: str) -> int:
        """软删除指定文档的所有向量"""
        async with AsyncSessionFactory() as session:
            stmt = (
                delete(DocumentEmbedding)
                .where(DocumentEmbedding.doc_id == doc_id)
            )
            result = await session.execute(stmt)
            await session.commit()
            count = result.rowcount
            logger.info("文档向量已删除: doc_id=%s count=%d", doc_id, count)
            return count

    # ─── 统计 ────────────────────────────────────────────────
    async def get_stats(self) -> dict[str, Any]:
        """获取知识库统计信息"""
        async with AsyncSessionFactory() as session:
            total = await session.scalar(
                select(func.count()).select_from(DocumentEmbedding).where(
                    DocumentEmbedding.is_active.is_(True)
                )
            )
            doc_count = await session.scalar(
                select(func.count(func.distinct(DocumentEmbedding.doc_id))).where(
                    DocumentEmbedding.is_active.is_(True)
                )
            )
        return {
            "total_vectors": total or 0,
            "total_documents": doc_count or 0,
            "embedding_dim": self._settings.EMBEDDING_DIM,
            "model": self._settings.EMBEDDING_MODEL,
        }


# ─── Init pgvector extension ────────────────────────────────
async def ensure_pgvector_extension() -> None:
    """确保 PostgreSQL 已启用 pgvector 扩展"""
    from database.models import async_engine
    async with async_engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    logger.info("pgvector 扩展已就绪")
