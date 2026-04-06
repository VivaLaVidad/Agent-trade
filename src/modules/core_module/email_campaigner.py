"""
modules.core_module.email_campaigner — 邮件营销暗箱模块
───────────────────────────────────────────────────────
职责：
  1. 从数据库加载目标客户线索
  2. 调用 Ollama 为每个客户生成个性化开发信
  3. 管理多阶段跟进序列（Day 0 首封 → Day 3 跟进 → Day 7 促单）
  4. 通过 gRPC RPAClient 调度 Web 邮箱发送
  5. 跟踪邮件状态（已发送 / 已回复）
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

from core.logger import get_logger

logger = get_logger(__name__)

_EMAIL_GEN_PROMPT: str = """\
You are an elite foreign trade email copywriter. Write a personalized \
cold outreach email based on the prospect data below.

Rules:
- Professional, warm, specific to their business
- Reference their company/industry naturally
- Include a clear call-to-action
- Keep under 200 words
- Output ONLY the email body (no subject line, no JSON)

Prospect data:
{prospect_json}

Sequence stage: {stage}
"""

_SEQUENCE_STAGES: list[dict[str, Any]] = [
    {"stage": "intro", "delay_days": 0, "subject_prefix": "Partnership Opportunity"},
    {"stage": "follow_up", "delay_days": 3, "subject_prefix": "Following Up"},
    {"stage": "closing", "delay_days": 7, "subject_prefix": "Last Chance"},
]


class EmailCampaigner:
    """邮件营销引擎 —— AI 个性化开发信 + 多阶段跟进序列"""

    async def execute(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        """执行邮件营销活动

        Parameters
        ----------
        ctx : AgentContext
        params : dict
            - campaign_name: str — 活动名称
            - lead_filter: dict — 线索筛选条件 (status/source/tags)
            - stages: list[str] — 要执行的阶段 ["intro","follow_up","closing"]
            - dry_run: bool — 仅生成不发送（默认 True）

        Returns
        -------
        dict
            {"campaign_id": str, "emails_generated": int, "emails_sent": int}
        """
        campaign_name: str = params.get("campaign_name", "unnamed")
        lead_filter: dict = params.get("lead_filter", {"status": "new"})
        stages: list[str] = params.get("stages", ["intro"])
        dry_run: bool = params.get("dry_run", True)

        logger.info("邮件营销启动: campaign=%s stages=%s dry_run=%s",
                     campaign_name, stages, dry_run)

        leads = await self._load_leads(lead_filter)
        if not leads:
            logger.info("无匹配线索，活动终止")
            return {"campaign_id": "", "emails_generated": 0, "emails_sent": 0}

        campaign_id = str(uuid.uuid4())
        emails_generated = 0
        emails_sent = 0

        for lead in leads:
            for stage_config in _SEQUENCE_STAGES:
                if stage_config["stage"] not in stages:
                    continue

                email_body = await self._generate_email(lead, stage_config["stage"])
                subject = f"{stage_config['subject_prefix']} — {lead.get('company', 'Your Business')}"

                emails_generated += 1

                if not dry_run and ctx.rpa_client and lead.get("client_email"):
                    sent = await self._send_email(ctx, lead, subject, email_body)
                    if sent:
                        emails_sent += 1

        await self._save_campaign(campaign_id, campaign_name, emails_generated, emails_sent)

        logger.info("邮件营销完成: generated=%d sent=%d", emails_generated, emails_sent)
        return {
            "campaign_id": campaign_id,
            "emails_generated": emails_generated,
            "emails_sent": emails_sent,
        }

    async def _load_leads(self, lead_filter: dict) -> list[dict[str, Any]]:
        try:
            from database.models import AsyncSessionFactory, ClientLead
            from sqlalchemy import select

            async with AsyncSessionFactory() as session:
                stmt = select(ClientLead).where(ClientLead.is_active.is_(True))
                if "status" in lead_filter:
                    stmt = stmt.where(ClientLead.status == lead_filter["status"])
                result = await session.execute(stmt.limit(50))
                rows = result.scalars().all()
                return [
                    {
                        "client_name": r.client_name,
                        "client_email": r.client_email,
                        "contact_info": r.contact_info,
                        "company": r.company,
                        "source": r.source,
                        "notes": r.notes,
                    }
                    for r in rows
                ]
        except Exception as exc:
            logger.error("加载线索失败: %s", exc)
            return []

    async def _generate_email(self, lead: dict, stage: str) -> str:
        llm = ChatOllama(model="qwen3:4b", temperature=0.7)
        prompt = _EMAIL_GEN_PROMPT.format(
            prospect_json=json.dumps(lead, ensure_ascii=False, default=str),
            stage=stage,
        )
        response = llm.invoke([
            SystemMessage(content=prompt),
            HumanMessage(content="Write the email now."),
        ])
        import re
        text = re.sub(r"<think>.*?</think>", "", response.content, flags=re.DOTALL)
        return text.strip()

    async def _send_email(
        self, ctx: Any, lead: dict, subject: str, body: str,
    ) -> bool:
        try:
            await ctx.rpa_client.execute_task(
                task_id=str(uuid.uuid4()),
                task_type="send_email",
                params={
                    "compose_url": "https://mail.google.com/mail/u/0/#inbox?compose=new",
                    "to": lead["client_email"],
                    "subject": subject,
                    "body": body,
                },
            )
            return True
        except Exception as exc:
            logger.error("邮件发送失败: %s", exc)
            return False

    async def _save_campaign(
        self, campaign_id: str, name: str, generated: int, sent: int,
    ) -> None:
        try:
            from database.models import AsyncSessionFactory, EmailCampaign

            async with AsyncSessionFactory() as session:
                campaign = EmailCampaign(
                    id=campaign_id,
                    name=name,
                    total_generated=generated,
                    total_sent=sent,
                    status="completed",
                )
                session.add(campaign)
                await session.commit()
        except Exception as exc:
            logger.error("保存营销活动记录失败: %s", exc)
