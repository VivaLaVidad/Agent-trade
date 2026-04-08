"""
TradeStealth_Core — FastAPI 启动入口
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
    logger.info("Project Claw 暗箱平台启动中...")

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
