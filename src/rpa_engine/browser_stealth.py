"""
rpa_engine.browser_stealth — 暗箱操作手 (Playwright Stealth)
─────────────────────────────────────────────────────────────
职责边界：
  1. 反检测浏览器实例管理（指纹伪装、WebDriver 隐藏）
  2. 模拟登录目标平台
  3. 自动发邮件（通过 Web 邮箱）
  4. 线索抓取 & 数据提取
  5. 统一任务调度接口
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)
from pydantic_settings import BaseSettings

from core.logger import get_logger
from core.demo_config import is_demo_mode

logger = get_logger(__name__)


# ─── Configuration ───────────────────────────────────────────
class RPASettings(BaseSettings):
    HEADLESS: bool = True
    SLOW_MO: int = 50
    DEFAULT_TIMEOUT: int = 30000
    USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    VIEWPORT_WIDTH: int = 1920
    VIEWPORT_HEIGHT: int = 1080
    PROXY_SERVER: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# ─── Stealth JavaScript Injection ────────────────────────────
_STEALTH_SCRIPTS = [
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});",
    """
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5].map(() => ({
            name: 'Chrome PDF Plugin',
            filename: 'internal-pdf-viewer',
        })),
    });
    """,
    "Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});",
    """
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : originalQuery(params);
    """,
    """
    window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {} };
    """,
]


# ═════════════════════════════════════════════════════════════
#  StealthBrowser — 反检测浏览器封装
# ═════════════════════════════════════════════════════════════
class StealthBrowser:
    """
    上下文管理器模式使用:
        async with StealthBrowser() as browser:
            result = await browser.execute_task(params)
    """

    def __init__(self, settings: Optional[RPASettings] = None):
        self._settings = settings or RPASettings()
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    async def __aenter__(self) -> StealthBrowser:
        await self._launch()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _launch(self) -> None:
        self._playwright = await async_playwright().start()

        launch_opts: dict[str, Any] = {
            "headless": self._settings.HEADLESS,
            "slow_mo": self._settings.SLOW_MO,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--disable-extensions",
            ],
        }
        if self._settings.PROXY_SERVER:
            launch_opts["proxy"] = {"server": self._settings.PROXY_SERVER}

        self._browser = await self._playwright.chromium.launch(**launch_opts)

        self._context = await self._browser.new_context(
            user_agent=self._settings.USER_AGENT,
            viewport={
                "width": self._settings.VIEWPORT_WIDTH,
                "height": self._settings.VIEWPORT_HEIGHT,
            },
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            permissions=["geolocation"],
        )
        self._context.set_default_timeout(self._settings.DEFAULT_TIMEOUT)

        await self._context.add_init_script(script="\n".join(_STEALTH_SCRIPTS))
        logger.info("Stealth 浏览器已启动")

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("浏览器已关闭")

    async def new_page(self) -> Page:
        if not self._context:
            raise RuntimeError("浏览器未初始化，请使用 async with 上下文管理器")
        return await self._context.new_page()

    # ─── 模拟人类行为工具 ────────────────────────────────────
    @staticmethod
    async def human_delay(min_ms: int = 500, max_ms: int = 2000) -> None:
        await asyncio.sleep(random.randint(min_ms, max_ms) / 1000)

    @staticmethod
    async def human_type(page: Page, selector: str, text: str) -> None:
        """逐字输入，模拟真实打字节奏"""
        await page.click(selector)
        for char in text:
            await page.keyboard.type(char, delay=random.randint(50, 150))
        await StealthBrowser.human_delay(300, 800)

    # ─── 核心任务执行器 ──────────────────────────────────────
    async def execute_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        统一任务分发入口
        params.task_type: login | send_email | scrape_leads
        """
        task_type = params.get("task_type", "")
        logger.info("执行 RPA 任务: type=%s", task_type)

        # ── Demo Mode: 拦截 Playwright 调用，返回模拟成功 ──
        if is_demo_mode():
            action = f"StealthBrowser.execute_task(type={task_type})"
            logger.info("[DEMO MODE] 物理暗箱操作已拦截 -> 虚拟执行动作: %s", action)
            await asyncio.sleep(1.5)
            return {"status": "demo_success", "task_type": task_type}

        dispatch = {
            "login": self._task_login,
            "send_email": self._task_send_email,
            "scrape_leads": self._task_scrape_leads,
        }

        handler = dispatch.get(task_type)
        if not handler:
            raise ValueError(f"未知任务类型: {task_type}")

        return await handler(params)

    async def _task_login(self, params: dict[str, Any]) -> dict[str, Any]:
        """模拟登录目标平台"""
        url = params["url"]
        username = params["username"]
        password = params["password"]

        page = await self.new_page()
        try:
            await page.goto(url, wait_until="networkidle")
            await self.human_delay()

            username_sel = params.get("username_selector", 'input[type="text"]')
            password_sel = params.get("password_selector", 'input[type="password"]')
            submit_sel = params.get("submit_selector", 'button[type="submit"]')

            await self.human_type(page, username_sel, username)
            await self.human_type(page, password_sel, password)
            await self.human_delay(1000, 2000)
            await page.click(submit_sel)
            await page.wait_for_load_state("networkidle")

            cookies = await self._context.cookies()
            logger.info("登录完成: url=%s cookies=%d", url, len(cookies))
            return {"status": "logged_in", "cookies_count": len(cookies)}
        finally:
            await page.close()

    async def _task_send_email(self, params: dict[str, Any]) -> dict[str, Any]:
        """通过 Web 邮箱发送邮件（需先登录）"""
        compose_url = params["compose_url"]
        to_addr = params["to"]
        subject = params["subject"]
        body = params["body"]

        page = await self.new_page()
        try:
            await page.goto(compose_url, wait_until="networkidle")
            await self.human_delay()

            to_sel = params.get("to_selector", 'input[name="to"]')
            subject_sel = params.get("subject_selector", 'input[name="subject"]')
            body_sel = params.get("body_selector", 'div[contenteditable="true"]')
            send_sel = params.get("send_selector", 'button:has-text("Send")')

            await self.human_type(page, to_sel, to_addr)
            await self.human_type(page, subject_sel, subject)
            await page.click(body_sel)
            await page.keyboard.type(body, delay=random.randint(20, 60))
            await self.human_delay(1000, 3000)
            await page.click(send_sel)
            await self.human_delay(2000, 4000)

            logger.info("邮件已发送: to=%s subject=%s", to_addr[:3] + "***", subject[:10])
            return {"status": "sent"}
        finally:
            await page.close()

    async def _task_scrape_leads(self, params: dict[str, Any]) -> dict[str, Any]:
        """抓取目标页面的销售线索"""
        url = params["url"]
        selectors = params.get("selectors", {})

        page = await self.new_page()
        try:
            await page.goto(url, wait_until="networkidle")
            await self.human_delay(2000, 4000)

            leads = []
            item_sel = selectors.get("item", ".lead-item")
            items = await page.query_selector_all(item_sel)

            for item in items:
                lead: dict[str, str] = {}
                for field_name, field_sel in selectors.get("fields", {}).items():
                    el = await item.query_selector(field_sel)
                    if el:
                        lead[field_name] = (await el.inner_text()).strip()
                if lead:
                    leads.append(lead)

            logger.info("线索抓取完成: url=%s count=%d", url, len(leads))
            return {"status": "scraped", "leads": leads, "total": len(leads)}
        finally:
            await page.close()
