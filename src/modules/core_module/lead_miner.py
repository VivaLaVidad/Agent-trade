"""
modules.core_module.lead_miner — 线索挖掘暗箱模块
──────────────────────────────────────────────────
职责：
  1. 接收目标关键词 / 行业 / 地区，调用 Ollama 生成搜索策略
  2. 通过 gRPC RPAClient 在 B2B 平台暗箱抓取潜在客户页面
  3. AI 解析原始数据为结构化 ClientLead 记录
  4. PII 字段自动 AES 加密入库
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from core.logger import get_logger

logger = get_logger(__name__)

_SEARCH_STRATEGY_PROMPT: str = """\
You are a B2B lead generation strategist. Given target parameters, \
generate 3-5 precise search queries that would find potential buyers \
on platforms like Alibaba, Made-in-China, Global Sources, or Google.

Output ONLY a JSON array of search query strings. Example:
["solar panel buyer India wholesale", "400W monocrystalline panel importer Mumbai"]"""

_LEAD_PARSE_PROMPT: str = """\
You are a data extraction specialist. Parse the following raw scraped text \
from a B2B platform and extract buyer leads as a JSON array.

Each lead object must have:
- "client_name": company or person name
- "client_email": email if found, null otherwise
- "contact_info": phone/whatsapp if found, null otherwise
- "company": company name
- "source": the platform name
- "notes": any useful context

Output ONLY valid JSON array. If no leads found, output [].

Raw text:
{raw_text}"""


class LeadMiner:
    """线索挖掘引擎 —— 从 B2B 平台暗箱提取潜在客户

    通过 AgentContext 获取 RPAClient 和数据库会话，
    全程不直接 import 基础设施模块。
    """

    async def execute(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        """执行线索挖掘任务

        Parameters
        ----------
        ctx : AgentContext
            统一上下文（含 cipher / rpa_client / recovery）
        params : dict
            挖掘参数:
            - keywords: list[str] — 目标关键词
            - industry: str — 目标行业
            - region: str — 目标地区
            - max_leads: int — 最大挖掘数量（默认 20）

        Returns
        -------
        dict[str, Any]
            {"leads_found": int, "leads": list[dict], "queries_used": list[str]}
        """
        keywords: list[str] = params.get("keywords", [])
        industry: str = params.get("industry", "general")
        region: str = params.get("region", "worldwide")
        max_leads: int = params.get("max_leads", 20)

        logger.info(
            "线索挖掘启动: keywords=%s industry=%s region=%s",
            keywords, industry, region,
        )

        search_queries = await self._generate_search_queries(
            keywords, industry, region,
        )
        logger.info("AI 生成 %d 条搜索策略", len(search_queries))

        all_leads: list[dict[str, Any]] = []

        for query in search_queries:
            if len(all_leads) >= max_leads:
                break

            raw_data = await self._scrape_via_rpa(ctx, query)
            if not raw_data:
                continue

            parsed = await self._parse_leads(raw_data)
            all_leads.extend(parsed)

        all_leads = all_leads[:max_leads]
        await self._persist_leads(ctx, all_leads)

        logger.info("线索挖掘完成: 共提取 %d 条线索", len(all_leads))
        return {
            "leads_found": len(all_leads),
            "leads": all_leads,
            "queries_used": search_queries,
        }

    async def _generate_search_queries(
        self,
        keywords: list[str],
        industry: str,
        region: str,
    ) -> list[str]:
        llm = ChatOllama(model="qwen3:4b", temperature=0.3)
        prompt = (
            f"Target keywords: {', '.join(keywords)}\n"
            f"Industry: {industry}\n"
            f"Region: {region}\n\n"
            f"Generate search queries."
        )
        response = llm.invoke([
            SystemMessage(content=_SEARCH_STRATEGY_PROMPT),
            HumanMessage(content=prompt),
        ])

        from agents.state import extract_json_from_llm
        try:
            result = extract_json_from_llm(response.content)
            if isinstance(result, list):
                return [str(q) for q in result]
            return result.get("queries", [])
        except (ValueError, AttributeError):
            return [f"{' '.join(keywords)} {industry} {region} buyer"]

    async def _scrape_via_rpa(self, ctx: Any, query: str) -> str:
        """通过 gRPC RPAClient 执行 B2B 平台抓取"""
        if ctx.rpa_client is None:
            logger.warning("RPAClient 未连接，使用模拟数据")
            return ""

        try:
            result = await ctx.rpa_client.execute_task(
                task_id=str(uuid.uuid4()),
                task_type="scrape_leads",
                params={
                    "url": f"https://www.google.com/search?q={query}",
                    "selectors": {"item": ".lead-item", "fields": {}},
                },
            )
            return json.dumps(result.get("leads", []))
        except Exception as exc:
            logger.error("RPA 抓取失败: %s", exc)
            return ""

    async def _parse_leads(self, raw_text: str) -> list[dict[str, Any]]:
        if not raw_text:
            return []

        llm = ChatOllama(model="qwen3:4b", temperature=0.1)
        response = llm.invoke([
            SystemMessage(content=_LEAD_PARSE_PROMPT.format(raw_text=raw_text[:3000])),
            HumanMessage(content="Parse the leads now."),
        ])

        from agents.state import extract_json_from_llm
        try:
            parsed = extract_json_from_llm(response.content)
            if isinstance(parsed, list):
                return parsed
            return parsed.get("leads", [])
        except (ValueError, AttributeError):
            return []

    async def _persist_leads(self, ctx: Any, leads: list[dict[str, Any]]) -> None:
        """将线索加密入库（通过 EncryptedString 自动 AES 加密 PII 字段）"""
        try:
            from database.models import AsyncSessionFactory, ClientLead

            async with AsyncSessionFactory() as session:
                for lead_data in leads:
                    lead = ClientLead(
                        client_name=lead_data.get("client_name", "Unknown"),
                        client_email=lead_data.get("client_email"),
                        contact_info=lead_data.get("contact_info"),
                        company=lead_data.get("company"),
                        source=lead_data.get("source", "lead_miner"),
                        status="new",
                        priority="medium",
                        notes=lead_data.get("notes"),
                    )
                    session.add(lead)
                await session.commit()
        except Exception as exc:
            logger.error("线索入库失败: %s", exc)
