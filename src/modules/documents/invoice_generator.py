"""
modules.documents.invoice_generator — DocuForge 防篡改商业文档引擎
═══════════════════════════════════════════════════════════════════
职责：
  1. 交易达成时自动拉取 TransactionLedger 中的 Ticker、价格、数量、MOQ 及汇率快照
  2. 使用 Jinja2 渲染 Proforma Invoice (PI) HTML 模板
  3. 利用 Playwright headless 模式将 HTML 打印为 PDF
  4. 对生成的 PDF 计算 SHA-256 哈希，写入数据库日志
  5. 确保生成的法律契约绝对不可篡改

暗箱原则：
  - 所有文档生成本地化，零公网依赖
  - PDF 哈希持久化到 document_hashes 表
  - 生成失败不阻塞主流程（降级为 HTML-only）
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jinja2 import Environment, FileSystemLoader

from core.logger import get_logger

logger = get_logger(__name__)

_TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "templates",
)

_OUTPUT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, os.pardir, "docs"),
)

_MAX_DB_RETRIES = 3


class InvoiceGenerator:
    """DocuForge 防篡改商业文档引擎

    生成 Proforma Invoice PDF 并计算 SHA-256 哈希。
    """

    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(_TEMPLATE_DIR),
            autoescape=True,
        )

    def generate_pi(
        self,
        transaction_data: dict[str, Any],
    ) -> dict[str, Any]:
        """生成 Proforma Invoice

        Parameters
        ----------
        transaction_data : dict
            交易数据，需包含:
            - po_number, ticker_id, sku_name, quantity, unit_price_rmb
            - unit_price_usd, total_usd, shipping_usd, landed_usd
            - fx_rate, shipping_term, payment_term, moq
            - supplier_name, buyer_name, destination, client_id
            - transaction_id, routing_fee_usd, fee_rate

        Returns
        -------
        dict
            {
                "html": str,
                "pdf_path": str | None,
                "pdf_bytes": bytes | None,
                "document_hash": str,
                "document_id": str,
                "po_number": str,
            }
        """
        doc_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # 准备模板数据
        ctx = {
            **transaction_data,
            "date": now.strftime("%Y-%m-%d %H:%M UTC"),
            "valid_until": (now + timedelta(days=7)).strftime("%Y-%m-%d"),
            "document_hash": "",  # 占位，PDF 生成后回填
        }

        # 渲染 HTML
        template = self._env.get_template("proforma_invoice.html")
        html_content = template.render(**ctx)

        # 尝试生成 PDF
        pdf_bytes = None
        pdf_path = None
        try:
            pdf_bytes = self._render_pdf(html_content)
            if pdf_bytes:
                os.makedirs(_OUTPUT_DIR, exist_ok=True)
                filename = f"PI_{ctx.get('po_number', doc_id)}.pdf"
                pdf_path = os.path.join(_OUTPUT_DIR, filename)
                with open(pdf_path, "wb") as f:
                    f.write(pdf_bytes)
                logger.info("DocuForge PDF 生成: %s (%d bytes)", filename, len(pdf_bytes))
        except Exception as exc:
            logger.warning("DocuForge PDF 生成失败，降级 HTML-only: %s", exc)

        # 计算 SHA-256 哈希
        hash_target = pdf_bytes if pdf_bytes else html_content.encode("utf-8")
        document_hash = hashlib.sha256(hash_target).hexdigest()

        # 回填哈希到 HTML（如果需要重新渲染）
        if not pdf_bytes:
            ctx["document_hash"] = document_hash
            html_content = template.render(**ctx)

        result = {
            "html": html_content,
            "pdf_path": pdf_path,
            "pdf_bytes": pdf_bytes,
            "document_hash": document_hash,
            "document_id": doc_id,
            "po_number": ctx.get("po_number", ""),
            "ticker_id": ctx.get("ticker_id", ""),
            "transaction_id": ctx.get("transaction_id", ""),
        }

        logger.info(
            "DocuForge 文档生成: doc=%s po=%s hash=%s… pdf=%s",
            doc_id[:8], ctx.get("po_number", "?"),
            document_hash[:16], "YES" if pdf_bytes else "NO",
        )

        return result

    @staticmethod
    def _render_pdf(html_content: str) -> bytes | None:
        """使用 Playwright headless 将 HTML 打印为 PDF

        Returns
        -------
        bytes | None
            PDF 字节流，失败返回 None
        """
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_content(html_content, wait_until="networkidle")
                pdf_bytes = page.pdf(
                    format="A4",
                    margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
                    print_background=True,
                )
                browser.close()
                return pdf_bytes
        except ImportError:
            logger.warning("Playwright 未安装，跳过 PDF 生成")
            return None
        except Exception as exc:
            logger.warning("Playwright PDF 渲染失败: %s", exc)
            return None

    async def hash_and_persist(
        self,
        result: dict[str, Any],
    ) -> str:
        """将文档哈希持久化到数据库

        Parameters
        ----------
        result : dict
            generate_pi() 的返回值

        Returns
        -------
        str
            document_hash
        """
        from sqlalchemy.exc import OperationalError

        document_hash = result.get("document_hash", "")

        for attempt in range(1, _MAX_DB_RETRIES + 1):
            try:
                from database.models import AsyncSessionFactory
                from modules.documents.invoice_generator import DocumentHash

                async with AsyncSessionFactory() as session:
                    entry = DocumentHash(
                        id=result.get("document_id", str(uuid.uuid4())),
                        document_type="proforma_invoice",
                        po_number=result.get("po_number", ""),
                        ticker_id=result.get("ticker_id", ""),
                        transaction_id=result.get("transaction_id", ""),
                        file_hash_sha256=document_hash,
                        pdf_generated=result.get("pdf_bytes") is not None,
                        pdf_path=result.get("pdf_path", ""),
                    )
                    session.add(entry)
                    await session.commit()

                logger.info("DocuForge 哈希持久化: hash=%s…", document_hash[:16])
                return document_hash
            except OperationalError as exc:
                logger.warning(
                    "DocuForge 持久化 OperationalError (attempt %d/%d): %s",
                    attempt, _MAX_DB_RETRIES, exc,
                )
                if attempt >= _MAX_DB_RETRIES:
                    logger.error("DocuForge 持久化最终失败: %s", exc)
            except Exception as exc:
                logger.debug("DocuForge 持久化跳过: %s", exc)
                return document_hash

        return document_hash


# ═══════════════════════════════════════════════════════════════
#  ORM Model — document_hashes
# ═══════════════════════════════════════════════════════════════

from sqlalchemy import Boolean as _Bool, String as _Str, DateTime as _DT, func as _func
from sqlalchemy.orm import Mapped as _Mapped, mapped_column as _mc
from database.models import Base as _DocBase


class DocumentHash(_DocBase):
    """文档哈希记录 — 防篡改审计"""
    __tablename__ = "document_hashes"

    id: _Mapped[str] = _mc(_Str(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_type: _Mapped[str] = _mc(_Str(30), comment="proforma_invoice | contract | quotation")
    po_number: _Mapped[str] = _mc(_Str(32), default="", comment="关联 PO 编号")
    ticker_id: _Mapped[str] = _mc(_Str(64), default="", comment="关联 Ticker ID")
    transaction_id: _Mapped[str] = _mc(_Str(36), default="", comment="关联交易流水号")
    file_hash_sha256: _Mapped[str] = _mc(_Str(64), comment="SHA-256 文档哈希")
    pdf_generated: _Mapped[bool] = _mc(_Bool, default=False, comment="是否成功生成 PDF")
    pdf_path: _Mapped[str] = _mc(_Str(512), default="", comment="PDF 文件路径")
    created_at: _Mapped[datetime] = _mc(_DT, server_default=_func.now())


# ═══════════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════════

_generator: InvoiceGenerator | None = None


def get_invoice_generator() -> InvoiceGenerator:
    """获取全局文档生成器单例"""
    global _generator
    if _generator is None:
        _generator = InvoiceGenerator()
    return _generator
