"""
OmniEdge (全域工联) — FastAPI 启动入口
仅绑定 127.0.0.1，拒绝一切外部访问
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

from core.logger import get_logger, sanitize_dict
from core.security import MachineAuth, require_machine_auth
from core.agent_context import AgentContext
from core.ticker_plant import get_market_bus
from agents.workflow_graph import WorkflowOrchestrator
from database.models import async_engine, create_tables
from database.pg_checkpointer import get_pg_checkpointer, shutdown_pg_checkpointer
from database.task_recovery import TaskRecoveryManager
from monitor.heartbeat import HeartbeatMonitor

logger = get_logger(__name__)

# ─── Rate Limiter ────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"error": "Terminal overloaded. Please wait for the current agent negotiations to settle."},
    )


# ─── Lifespan ────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("OmniEdge (全域工联) 平台启动中...")

    # 1. 许可证校验（DEMO_MODE 跳过）
    from core.demo_config import is_demo_mode
    if is_demo_mode():
        logger.info("DEMO_MODE 已启用 — 跳过许可证校验")
    else:
        try:
            from modules.audit_module.hardware_license import LicenseManager, LicenseError
            license_mgr = LicenseManager()
            lic = license_mgr.validate()
            logger.info("许可证有效: licensee=%s", lic.licensee)
        except LicenseError as exc:
            logger.error("许可证验证失败: %s — 系统拒绝启动", exc)
            raise SystemExit(1) from exc
        except FileNotFoundError:
            logger.warning("许可证文件不存在，首次运行自动生成...")
            LicenseManager().generate_license_file()

    # 2. 断电恢复
    recovery = TaskRecoveryManager()
    interrupted = recovery.recover_on_startup()
    if interrupted:
        logger.warning("已恢复 %d 个中断任务至待执行队列", len(interrupted))

    # 3. 心跳监控
    heartbeat = HeartbeatMonitor()
    await heartbeat.start()

    # 3.5 RPA Worker 容灾监控
    from monitor.service_watchdog import ServiceWatchdog
    watchdog = ServiceWatchdog()
    await watchdog.start()
    app.state.watchdog = watchdog

    # 4. AgentContext 组装 + 模块自动发现
    ctx = AgentContext.build(recovery=recovery, heartbeat=heartbeat)
    app.state.ctx = ctx
    app.state.recovery = recovery
    app.state.heartbeat = heartbeat

    # 5. 数据库 + LangGraph 检查点预热 + 工作流编排器（须先 PG 池再构图）
    await create_tables()
    await get_pg_checkpointer()
    app.state.orchestrator = WorkflowOrchestrator()

    # 6. MarketDataBus 启动（Ticker Plant 事件总线）
    market_bus = get_market_bus()
    await market_bus.start()
    app.state.market_bus = market_bus

    logger.info("Project Claw 系统就绪 — 已加载模块: %s",
                ", ".join(ctx.registry.list_all()))

    yield

    await market_bus.stop()
    await watchdog.stop()
    await heartbeat.stop()
    await shutdown_pg_checkpointer()
    await async_engine.dispose()
    logger.info("系统已关闭")


# ─── CORS Origins ────────────────────────────────────────────
_DEFAULT_ORIGINS = "http://127.0.0.1:3000,http://localhost:3000"
ALLOWED_ORIGINS: list[str] = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if o.strip()
]

# ─── App ─────────────────────────────────────────────────────
app = FastAPI(
    title="TradeStealth_Core",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS: 使用 ALLOWED_ORIGINS 环境变量，禁止 *
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Hardware-Token", "Authorization"],
)


# ─── Request / Response Schemas ──────────────────────────────
class TradeRequest(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=64)
    intent_text: str = Field(..., min_length=1, max_length=4096)
    context: dict = Field(default_factory=dict)


class TradeResponse(BaseModel):
    session_id: str
    status: str
    result: dict


# ─── Routes ──────────────────────────────────────────────────
@app.post(
    "/api/v1/execute",
    response_model=TradeResponse,
    dependencies=[Depends(require_machine_auth)],
)
@limiter.limit("3/minute")
async def execute_workflow(req: TradeRequest, request: Request) -> TradeResponse:
    """主执行入口：接收意图 → Agent 编排 → 返回结果"""
    logger.info("收到请求 session=%s", req.session_id)
    logger.debug("请求上下文: %s", sanitize_dict(req.context))

    orchestrator: WorkflowOrchestrator = request.app.state.orchestrator
    try:
        result = await orchestrator.run(
            session_id=req.session_id,
            intent_text=req.intent_text,
            context=req.context,
        )
    except Exception as exc:
        logger.exception("工作流执行异常 session=%s", req.session_id)
        raise HTTPException(status_code=500, detail="内部处理异常") from exc

    return TradeResponse(
        session_id=req.session_id,
        status="completed",
        result=result,
    )








# ─── Buyer Inquire (Graph Suspension) ────────────────────────
class InquireRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2048)
    target: str = Field(default="VN", max_length=4)
    quantity: int = Field(default=100, ge=1)


class InquireResponse(BaseModel):
    thread_id: str
    status: str
    source_type: str
    candidates: list[dict]
    sell_side_transaction_id: str


@app.post("/api/v1/buyer/inquire", response_model=InquireResponse)
@limiter.limit("10/minute")
async def buyer_inquire(req: InquireRequest, request: Request) -> InquireResponse:
    """C-side inquiry: init matching graph, run to sourcing pause, return candidates"""
    import uuid
    from modules.supply_chain.matching_graph import build_matching_graph

    thread_id = str(uuid.uuid4())
    txn_id = f"TXN-{thread_id[:8].upper()}"

    # Build a fresh graph for this inquiry (with interrupt_before)
    graph = build_matching_graph()

    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {
        "raw_input": req.query,
        "sell_side_transaction_id": txn_id,
    }

    try:
        # Graph will pause at interrupt_before=["risk_defense_node"]
        result = await graph.ainvoke(initial_state, config=config)
    except Exception:
        # Graph interrupted (expected) — read current state
        try:
            snapshot = await graph.aget_state(config)
            result = dict(snapshot.values) if snapshot and snapshot.values else {}
        except Exception:
            result = {}

    candidates = result.get("candidates", [])
    source_type = result.get("source_type", "UNKNOWN")

    return InquireResponse(
        thread_id=thread_id,
        status="awaiting_confirmation" if candidates else "no_candidates",
        source_type=source_type,
        candidates=candidates[:5],
        sell_side_transaction_id=txn_id,
    )


class ConfirmTradeRequest(BaseModel):
    thread_id: str = Field(..., min_length=1)
    selected_quote_id: str = Field(default="")
    sell_side_transaction_id: str = Field(default="")


class ConfirmTradeResponse(BaseModel):
    status: str
    thread_id: str
    message: str


@app.post("/api/v1/buyer/confirm-trade", response_model=ConfirmTradeResponse)
@limiter.limit("5/minute")
async def buyer_confirm_trade(req: ConfirmTradeRequest, request: Request) -> ConfirmTradeResponse:
    """C-side confirmation: resume suspended graph, execute to END, publish event"""
    from modules.supply_chain.matching_graph import build_matching_graph
    from core.ticker_plant import get_market_bus, MarketEvent, EventType

    graph = build_matching_graph()
    config = {"configurable": {"thread_id": req.thread_id}}

    try:
        # Resume graph with buyer confirmation injected
        resume_state = {
            "buyer_confirmation": {"selected_quote_id": req.selected_quote_id},
        }
        result = await graph.ainvoke(resume_state, config=config)

        # Publish NEW_TRADE_EXECUTED event
        bus = get_market_bus()
        event = MarketEvent(
            event_type=EventType.NEW_TRADE_EXECUTED,
            ticker_id=f"CLAW-TRADE-{req.thread_id[:8].upper()}",
            data={
                "thread_id": req.thread_id,
                "sell_side_transaction_id": req.sell_side_transaction_id,
                "status": result.get("status", "completed"),
                "selected_quote_id": req.selected_quote_id,
            },
        )
        await bus.publish(event)

        return ConfirmTradeResponse(
            status="trade_executed",
            thread_id=req.thread_id,
            message="Order confirmed. PI generated with SHA-256 hash. Merchant notified.",
        )
    except Exception as exc:
        logger.exception("confirm-trade failed: thread=%s", req.thread_id)
        return ConfirmTradeResponse(
            status="error",
            thread_id=req.thread_id,
            message=f"Trade confirmation failed: {str(exc)[:200]}",
        )


# ─── Merchant SSE Stream ────────────────────────────────────
from starlette.responses import StreamingResponse
import asyncio as _sse_asyncio


@app.get("/api/v1/merchant/stream")
async def merchant_sse_stream(request: Request):
    """B-side SSE: subscribe to NEW_TRADE_EXECUTED events via MarketDataBus"""
    from core.ticker_plant import get_market_bus, EventType

    bus = get_market_bus()
    event_queue: _sse_asyncio.Queue = _sse_asyncio.Queue()

    async def _on_trade(event):
        if event.event_type == EventType.NEW_TRADE_EXECUTED:
            await event_queue.put(event)

    bus.subscribe("CLAW-TRADE-*", _on_trade)

    async def _generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await _sse_asyncio.wait_for(event_queue.get(), timeout=30.0)
                    import json
                    data = json.dumps(event.to_dict(), default=str)
                    yield f"event: new_trade\ndata: {data}\n\n"
                except _sse_asyncio.TimeoutError:
                    yield f": keepalive\n\n"
        finally:
            bus.unsubscribe("CLAW-TRADE-*", _on_trade)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Flash Intent (H5 Buyer Portal) ─────────────────────────
class FlashIntentRequest(BaseModel):
    sku: str = Field(..., min_length=1, max_length=256)
    quantity: int = Field(default=100, ge=1)
    target_country: str = Field(default="VN", max_length=4)
    is_urgent: bool = Field(default=False)


class FlashIntentResponse(BaseModel):
    status: str
    source_type: str
    sku_match: dict | None
    estimated_delivery: str
    is_un_certified: bool
    is_rcep_eligible: bool
    recommendation: str


@app.post("/api/v1/buyer/flash-intent", response_model=FlashIntentResponse)
@limiter.limit("10/minute")
async def flash_intent(req: FlashIntentRequest, request: Request) -> FlashIntentResponse:
    """H5 Flash Intent: lightweight procurement entry for SEA buyers"""
    from database.mock_inventory import get_mock_inventory

    inventory = get_mock_inventory()
    hits = inventory.query(sku_name=req.sku, qty=req.quantity)

    if not hits:
        return FlashIntentResponse(
            status="no_match",
            source_type="REMOTE_ARBITRAGE",
            sku_match=None,
            estimated_delivery="3-5 Business Days (sourcing required)",
            is_un_certified=False,
            is_rcep_eligible=False,
            recommendation=f"No local inventory for '{req.sku}'. Scatter broadcast initiated to external suppliers.",
        )

    best = hits[0]
    delivery = "24 Hours (in-stock)" if req.is_urgent and best["stock_qty"] >= req.quantity else "2-3 Business Days"

    return FlashIntentResponse(
        status="matched",
        source_type="LOCAL_INVENTORY",
        sku_match={
            "sku_id": best["sku_id"],
            "sku_name": best["sku_name"],
            "unit_price_usd": best["cost_price"],
            "stock_qty": best["stock_qty"],
            "profit_margin_pct": best["profit_margin_pct"],
            "location": best["location"],
        },
        estimated_delivery=delivery,
        is_un_certified=best.get("is_un_certified", True),
        is_rcep_eligible=best.get("is_rcep_eligible", True),
        recommendation=f"Local match found: {best['sku_name']} @ ${best['cost_price']:.4f}/unit. "
                       f"{'UN Certified + RCEP 0% Tariff eligible.' if best.get('is_rcep_eligible') else ''} "
                       f"Margin: {best['profit_margin_pct']:.1f}%.",
    )


# ─── DeepSeek AI Buyer Recommendation ───────────────────────
class AIRecommendRequest(BaseModel):
    sku_name: str = Field(..., min_length=1, max_length=256)
    quantity: int = Field(default=1000, ge=1)
    target_country: str = Field(default="VN", max_length=4)
    unit_price_usd: float = Field(default=0.0, ge=0)


class AIRecommendResponse(BaseModel):
    ai_recommendation: str
    risk_notes: str
    alternative_suggestions: list[str]
    status: str


@app.post("/api/v1/buyer/ai-recommend", response_model=AIRecommendResponse)
@limiter.limit("5/minute")
async def buyer_ai_recommend(req: AIRecommendRequest, request: Request) -> AIRecommendResponse:
    """DeepSeek AI-powered procurement recommendation for buyer portal"""
    import httpx
    from pydantic_settings import BaseSettings

    class _DSSettings(BaseSettings):
        DEEPSEEK_API_KEY: str = ""
        DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
        DEEPSEEK_MODEL: str = "deepseek-chat"
        model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    ds = _DSSettings()
    if not ds.DEEPSEEK_API_KEY:
        return AIRecommendResponse(
            ai_recommendation="AI recommendation unavailable — DeepSeek API key not configured.",
            risk_notes="",
            alternative_suggestions=[],
            status="no_api_key",
        )

    prompt = (
        f"You are an expert industrial procurement advisor for OmniEdge (全域工联), "
        f"a cross-border B2B trade platform specializing in ASEAN markets.\n\n"
        f"Product: {req.sku_name}\n"
        f"Quantity: {req.quantity} units\n"
        f"Unit Price: ${req.unit_price_usd:.4f}\n"
        f"Target Country: {req.target_country}\n\n"
        f"Provide a concise procurement recommendation (2-3 sentences) covering:\n"
        f"1. Price competitiveness assessment\n"
        f"2. Compliance/certification notes for the target country\n"
        f"3. Any risk factors\n\n"
        f"Also suggest 2-3 alternative components if applicable.\n"
        f"Reply in English, be concise and professional."
    )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{ds.DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {ds.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": ds.DEEPSEEK_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 500,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            # Split content into recommendation and alternatives
            lines = content.split("\n")
            main_rec = []
            risk = []
            alts = []
            section = "main"
            for line in lines:
                l_lower = line.lower().strip()
                if "risk" in l_lower or "warning" in l_lower or "caution" in l_lower:
                    section = "risk"
                elif "alternative" in l_lower or "substitute" in l_lower:
                    section = "alt"

                if section == "main":
                    main_rec.append(line)
                elif section == "risk":
                    risk.append(line)
                elif section == "alt":
                    stripped = line.strip().lstrip("-•·").strip()
                    if stripped and "alternative" not in stripped.lower():
                        alts.append(stripped)

            return AIRecommendResponse(
                ai_recommendation="\n".join(main_rec).strip() or content[:300],
                risk_notes="\n".join(risk).strip(),
                alternative_suggestions=alts[:3],
                status="ok",
            )
    except Exception as exc:
        logger.warning("DeepSeek AI recommendation failed: %s", exc)
        return AIRecommendResponse(
            ai_recommendation="AI analysis temporarily unavailable. Proceed with standard matching.",
            risk_notes="",
            alternative_suggestions=[],
            status="error",
        )


# ─── ASKB (Agentic Trader Copilot) ──────────────────────────
class ASKBRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2048)


class ASKBResponse(BaseModel):
    intent: str
    tool_used: str | None
    data: dict
    recommendation: str
    status: str


@app.post("/api/v1/askb/query", response_model=ASKBResponse)
@limiter.limit("10/minute")
async def askb_query(req: ASKBRequest, request: Request) -> ASKBResponse:
    """ASKB Trader Copilot: natural language query for merchant operators"""
    from modules.agents.askb_agent import get_askb_copilot

    copilot = get_askb_copilot()
    try:
        result = await copilot.process(req.query)
    except Exception as exc:
        logger.exception("ASKB query failed: %s", req.query[:80])
        raise HTTPException(status_code=500, detail="ASKB processing error") from exc

    return ASKBResponse(**result)


# ─── Ticker Tape (Homepage) ──────────────────────────────────
@app.get("/api/v1/ticker/tape")
@limiter.limit("30/minute")
async def ticker_tape(request: Request) -> list[dict]:
    """Return ticker tape data from TickerRegistry for homepage marquee"""
    from core.ticker_plant import get_ticker_registry
    import random

    registry = get_ticker_registry()
    all_tickers = registry.search("", limit=50)

    if all_tickers:
        tape = []
        for t in all_tickers[:12]:
            tape.append({
                "symbol": t.ticker_id,
                "price": round(random.uniform(0.01, 5000.0), 2),
                "change": round(random.uniform(-5.0, 5.0), 1),
            })
        return tape

    # Fallback: static ticker data when registry is empty
    return [
        {"symbol": "CLAW-ELEC-5GCPE", "price": 128.00, "change": 2.3},
        {"symbol": "CLAW-MECH-CNC01", "price": 4520.00, "change": -0.8},
        {"symbol": "CLAW-TELE-5GANT", "price": 890.50, "change": 1.2},
        {"symbol": "CLAW-MINE-SAFETY", "price": 2340.00, "change": 0.5},
        {"symbol": "CLAW-SOLAR-PV500", "price": 156.80, "change": -1.1},
        {"symbol": "CLAW-INDU-ROBOT", "price": 12450.00, "change": 3.4},
        {"symbol": "CLAW-TRANS-LOGIS", "price": 892.00, "change": 0.2},
        {"symbol": "CLAW-CHEM-REACT", "price": 3450.00, "change": -0.3},
    ]


# ─── Admin KPIs ──────────────────────────────────────────────
@app.get("/api/v1/admin/kpis")
@limiter.limit("20/minute")
async def admin_kpis(request: Request) -> dict:
    """Return dashboard KPIs — reads from DB when available, falls back to mock"""
    try:
        from database.models import AsyncSessionFactory
        from sqlalchemy import text

        async with AsyncSessionFactory() as session:
            row = await session.execute(text("SELECT count(*) FROM intent_records"))
            total_inquiries = row.scalar() or 0
            row2 = await session.execute(text(
                "SELECT count(*) FROM strategy_records WHERE status = 'HEDGE_LOCKED'"
            ))
            hedge_success = row2.scalar() or 0
            row3 = await session.execute(text(
                "SELECT count(*) FROM rpa_logs WHERE status = 'BLOCKED'"
            ))
            regguard_blocks = row3.scalar() or 0

        rate = round((hedge_success / max(total_inquiries, 1)) * 100, 1)
        return {
            "total_inquiries": total_inquiries,
            "hedge_success": hedge_success,
            "hedge_success_rate": rate,
            "regguard_blocks": regguard_blocks,
            "block_types": {"embargo": 0, "dual_use": 0, "sanctions": 0, "other": regguard_blocks},
            "inquiries_trend": [],
        }
    except Exception:
        # DB not available — return mock KPIs
        return {
            "total_inquiries": 1247,
            "hedge_success": 892,
            "hedge_success_rate": 71.5,
            "regguard_blocks": 43,
            "block_types": {"embargo": 18, "dual_use": 12, "sanctions": 8, "other": 5},
            "inquiries_trend": [82, 95, 78, 110, 103, 125, 98, 134, 112, 145, 128, 137],
        }


@app.get("/health")
async def health_check() -> dict:
    return {"status": "ok", "machine_bound": MachineAuth.get_machine_id()[:8] + "****"}


# ─── Entrypoint ──────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8900,
        reload=False,
        log_level="info",
        access_log=False,
    )
