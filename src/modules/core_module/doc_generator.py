"""
modules.core_module.doc_generator — 报价单/合同生成暗箱模块
─────────────────────────────────────────────────────────
职责：
  1. 接收客户意图分析结果 + 产品目录数据
  2. 调用 Ollama 生成报价单 / 形式发票 / 合同文本
  3. AES 加密文档内容入库存储
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from core.logger import get_logger

logger = get_logger(__name__)

_DOC_GEN_PROMPT: str = """\
You are a professional foreign trade document specialist.

Generate a {doc_type} document based on the following data.
Output a well-structured document in plain text with clear sections.

For quotations: include item descriptions, quantities, unit prices, \
total, payment terms, delivery terms (FOB/CIF), validity period.

For proforma invoices: include buyer/seller info, itemized list, \
banking details placeholder, shipping terms.

For contracts: include parties, scope, pricing, delivery, warranty, \
dispute resolution, signatures placeholder.

Client data:
{client_json}

Product/intent data:
{intent_json}
"""


class DocGenerator:
    """商业文档生成引擎 —— AI 驱动的报价单/发票/合同生成器"""

    async def execute(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        """生成商业文档

        Parameters
        ----------
        ctx : AgentContext
        params : dict
            - doc_type: str — "quotation" | "proforma_invoice" | "contract"
            - client_data: dict — 客户信息
            - intent_data: dict — 意图/产品数据
            - encrypt: bool — 是否加密存储（默认 True）

        Returns
        -------
        dict
            {"doc_id": str, "doc_type": str, "content_preview": str}
        """
        doc_type: str = params.get("doc_type", "quotation")
        client_data: dict = params.get("client_data", {})
        intent_data: dict = params.get("intent_data", {})
        encrypt: bool = params.get("encrypt", True)

        logger.info("文档生成启动: type=%s client=%s",
                     doc_type, client_data.get("client_name", "N/A"))

        content = await self._generate_document(doc_type, client_data, intent_data)
        doc_id = str(uuid.uuid4())

        await self._persist_document(ctx, doc_id, doc_type, client_data, content, encrypt)

        logger.info("文档生成完成: id=%s type=%s length=%d",
                     doc_id[:8], doc_type, len(content))

        return {
            "doc_id": doc_id,
            "doc_type": doc_type,
            "content_preview": content[:300] + "..." if len(content) > 300 else content,
        }

    async def _generate_document(
        self,
        doc_type: str,
        client_data: dict,
        intent_data: dict,
    ) -> str:
        llm = ChatOllama(model="qwen3:4b", temperature=0.4)
        prompt = _DOC_GEN_PROMPT.format(
            doc_type=doc_type.replace("_", " ").title(),
            client_json=json.dumps(client_data, ensure_ascii=False, default=str),
            intent_json=json.dumps(intent_data, ensure_ascii=False, default=str),
        )
        response = llm.invoke([
            SystemMessage(content=prompt),
            HumanMessage(content=f"Generate the {doc_type} now."),
        ])
        import re
        return re.sub(r"<think>.*?</think>", "", response.content, flags=re.DOTALL).strip()

    async def _persist_document(
        self,
        ctx: Any,
        doc_id: str,
        doc_type: str,
        client_data: dict,
        content: str,
        encrypt: bool,
    ) -> None:
        try:
            from database.models import AsyncSessionFactory, GeneratedDocument

            stored_content = content
            if encrypt:
                import base64
                encrypted = ctx.cipher.encrypt_string(content)
                stored_content = base64.b64encode(encrypted).decode("ascii")

            async with AsyncSessionFactory() as session:
                doc = GeneratedDocument(
                    id=doc_id,
                    doc_type=doc_type,
                    client_name=client_data.get("client_name", ""),
                    content=stored_content,
                    is_encrypted=encrypt,
                )
                session.add(doc)
                await session.commit()
        except Exception as exc:
            logger.error("文档入库失败: %s", exc)
