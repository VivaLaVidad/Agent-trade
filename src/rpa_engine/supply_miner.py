"""
rpa_engine.supply_miner — 幽灵矿工 (Ghost Miner) 供应链底价采集引擎
═══════════════════════════════════════════════════════════════════════
职责：
  1. 隐身浏览器：封装 StealthBrowser，反反爬模式 + 代理支持
  2. 网页清洗器：DOM → 干净 Markdown (BeautifulSoup + markdownify)
  3. LLM 结构化提取：DeepSeek API (JSON Mode) → List[SupplierQuote]
  4. 提供 mock 模式用于离线测试和演示

暗箱原则：
  - 所有抓取操作通过 StealthBrowser 反检测
  - 代理通过 .env PROXY_URL 加载
  - 15 秒超时硬限制
  - 抓取失败优雅降级，不阻塞主流程

数据模型::

    SupplierQuote:
      component_name: str   — 品名
      supplier_name: str    — 供应商
      moq: int              — 最小起订量
      price_tiers: dict     — 阶梯价格 {"1+": 0.5, "100+": 0.4, "1000+": 0.35}
      stock_status: str     — 库存状态 (in_stock / low_stock / out_of_stock)
      source_url: str       — 数据来源 URL
      scraped_at: str       — 采集时间 ISO8601
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote, urlparse

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from core.logger import get_logger

logger = get_logger(__name__)

_SCRAPE_TIMEOUT_SEC = 15
_MAX_FETCH_URLS = 3
_DEFAULT_SEARCH_TEMPLATE = "https://www.szlcsc.com/so/{query}/catalog.html"


def _is_safe_fetch_url(url: str) -> bool:
    """仅允许公网 http(s)，降低 SSRF / file:// / 内网探测风险（live 模式）。"""
    try:
        p = urlparse(url.strip())
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False
    if host.endswith(".local") or host.endswith(".localhost"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass
    return True


def _normalize_target_urls(target_urls: list[str] | None, query: str) -> list[str]:
    """合并用户 URL（过滤后）或生成默认 LCSC 搜索链接（query 已 URL 编码）。"""
    if target_urls:
        safe = [u for u in target_urls if _is_safe_fetch_url(u)][: _MAX_FETCH_URLS]
        if len(safe) < len(target_urls):
            logger.warning(
                "幽灵矿工: 已丢弃 %d 个不安全的 target_urls（仅允许公网 http/https）",
                len(target_urls) - len(safe),
            )
        return safe
    q = quote(query.strip(), safe="")
    if not q:
        return []
    return [_DEFAULT_SEARCH_TEMPLATE.format(query=q)]


# ═══════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════

class MinerSettings(BaseSettings):
    """幽灵矿工配置"""
    PROXY_URL: str = ""
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    MINER_MODE: str = "mock"  # mock | live
    MINER_TIMEOUT_SEC: int = _SCRAPE_TIMEOUT_SEC

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


# ═══════════════════════════════════════════════════════════════
#  Pydantic Models
# ═══════════════════════════════════════════════════════════════

class SupplierQuote(BaseModel):
    """供应商报价结构化数据"""
    component_name: str = Field(..., description="品名 (如 100nF 50V 0805 MLCC)")
    supplier_name: str = Field(..., description="供应商名称")
    moq: int = Field(default=1, description="最小起订量")
    price_tiers: dict[str, float] = Field(
        default_factory=dict,
        description="阶梯价格 {'1+': 0.5, '100+': 0.4, '1000+': 0.35}",
    )
    stock_status: str = Field(
        default="unknown",
        description="库存状态: in_stock | low_stock | out_of_stock | unknown",
    )
    source_url: str = Field(default="", description="数据来源 URL")
    scraped_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="采集时间 ISO8601",
    )
    unit_price_rmb: float = Field(default=0.0, description="最低单价 (RMB)")
    currency: str = Field(default="CNY", description="货币")


class MinerResult(BaseModel):
    """矿工采集结果"""
    query: str
    quotes: list[SupplierQuote] = Field(default_factory=list)
    source: str = "mock"  # mock | live | cache
    raw_markdown: str = ""
    error: str = ""
    elapsed_sec: float = 0.0


# ═══════════════════════════════════════════════════════════════
#  Web Cleaner — DOM → Markdown
# ═══════════════════════════════════════════════════════════════

class WebCleaner:
    """网页清洗器：将复杂 DOM 转换为 LLM 友好的干净 Markdown"""

    @staticmethod
    def html_to_markdown(html: str) -> str:
        """将 HTML 转换为干净 Markdown，剔除 JS/CSS/导航等噪声

        Parameters
        ----------
        html : str
            原始 HTML 内容

        Returns
        -------
        str
            干净的 Markdown 文本
        """
        try:
            from bs4 import BeautifulSoup
            from markdownify import markdownify as md
        except ImportError:
            # 降级：简单正则清洗
            return WebCleaner._fallback_clean(html)

        soup = BeautifulSoup(html, "html.parser")

        # 移除噪声元素
        for tag in soup.find_all(["script", "style", "nav", "footer", "header",
                                   "iframe", "noscript", "svg", "form"]):
            tag.decompose()

        # 移除所有 class/id/style 属性中的广告相关元素
        for tag in soup.find_all(True):
            classes = tag.get("class", [])
            if isinstance(classes, list):
                class_str = " ".join(classes).lower()
            else:
                class_str = str(classes).lower()
            if any(kw in class_str for kw in ["ad", "banner", "popup", "cookie", "modal"]):
                tag.decompose()

        # 转换为 Markdown
        markdown = md(str(soup), heading_style="ATX", strip=["img", "a"])

        # 清理多余空行
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)
        markdown = markdown.strip()

        # 截断过长内容 (LLM context window 限制)
        if len(markdown) > 8000:
            markdown = markdown[:8000] + "\n\n[... 内容已截断 ...]"

        return markdown

    @staticmethod
    def _fallback_clean(html: str) -> str:
        """降级清洗：无 BeautifulSoup 时使用正则"""
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.I)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000]


# ═══════════════════════════════════════════════════════════════
#  LLM Extractor — DeepSeek JSON Mode
# ═══════════════════════════════════════════════════════════════

_EXTRACT_PROMPT = """\
You are an electronic components procurement data extractor.
From the following Markdown text (scraped from a supplier website), extract the TOP 3 cheapest supplier quotes.

For each quote, extract:
- component_name: exact component name/model
- supplier_name: supplier/store name
- moq: minimum order quantity (integer)
- price_tiers: pricing tiers as dict, e.g. {"1+": 0.50, "100+": 0.40, "1000+": 0.35}
- stock_status: "in_stock" | "low_stock" | "out_of_stock" | "unknown"
- unit_price_rmb: the lowest unit price in RMB (float)

Output ONLY a JSON object with a single key "quotes" whose value is an array
of up to 3 objects (same schema as above). Example: {{"quotes": [...]}}
No markdown fences, no other keys. If no valid quotes: {{"quotes": []}}

Markdown content:
{markdown}
"""


class LLMExtractor:
    """DeepSeek API 结构化提取器 (JSON Mode)"""

    def __init__(self, settings: MinerSettings | None = None) -> None:
        self._settings = settings or MinerSettings()

    async def extract_quotes(
        self,
        markdown: str,
        query: str = "",
    ) -> list[SupplierQuote]:
        """从 Markdown 文本中提取供应商报价

        Parameters
        ----------
        markdown : str
            清洗后的 Markdown 文本
        query : str
            原始搜索查询

        Returns
        -------
        list[SupplierQuote]
            提取的供应商报价列表 (最多 3 个)
        """
        if not self._settings.DEEPSEEK_API_KEY:
            logger.info("DeepSeek API Key 未配置，使用 Ollama 本地模型")
            return await self._extract_with_ollama(markdown, query)

        return await self._extract_with_deepseek(markdown, query)

    async def _extract_with_deepseek(
        self, markdown: str, query: str,
    ) -> list[SupplierQuote]:
        """使用 DeepSeek API 提取"""
        try:
            import httpx

            prompt = _EXTRACT_PROMPT.format(markdown=markdown[:6000])
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._settings.DEEPSEEK_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._settings.DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._settings.DEEPSEEK_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.1,
                        "max_tokens": 2000,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return self._parse_response(content)
        except Exception as exc:
            logger.warning("DeepSeek 提取失败，回退 Ollama: %s", exc)
            return await self._extract_with_ollama(markdown, query)

    async def _extract_with_ollama(
        self, markdown: str, query: str,
    ) -> list[SupplierQuote]:
        """使用本地 Ollama 模型提取"""
        try:
            from langchain_ollama import ChatOllama
            from langchain_core.messages import HumanMessage

            llm = ChatOllama(model="qwen3:4b", temperature=0.1, format="json")
            prompt = _EXTRACT_PROMPT.format(markdown=markdown[:4000])
            resp = llm.invoke([HumanMessage(content=prompt)])
            content = re.sub(r"<think>.*?</think>", "", resp.content, flags=re.DOTALL).strip()
            return self._parse_response(content)
        except Exception as exc:
            logger.warning("Ollama 提取失败: %s", exc)
            return []

    @staticmethod
    def _parse_response(content: str) -> list[SupplierQuote]:
        """解析 LLM JSON 响应为 SupplierQuote 列表"""
        try:
            data = json.loads(content)
            if isinstance(data, list):
                raw_list = data
            elif isinstance(data, dict):
                raw_list = data.get("quotes", data.get("results", data.get("data", [])))
                if not isinstance(raw_list, list):
                    return []
            else:
                return []
            quotes = []
            for item in raw_list[:3]:
                try:
                    q = SupplierQuote(**item)
                    # 自动计算最低单价
                    if q.price_tiers and q.unit_price_rmb == 0:
                        q.unit_price_rmb = min(q.price_tiers.values())
                    quotes.append(q)
                except Exception:
                    continue
            return quotes
        except json.JSONDecodeError:
            return []


# ═══════════════════════════════════════════════════════════════
#  Mock Data — 离线测试/演示
# ═══════════════════════════════════════════════════════════════

def _generate_mock_quotes(query: str) -> list[SupplierQuote]:
    """生成模拟供应商报价 (确定性，基于查询哈希)"""
    import hashlib

    h = int(hashlib.md5(query.lower().encode()).hexdigest()[:8], 16)
    base_price = 0.1 + (h % 100) / 100.0

    return [
        SupplierQuote(
            component_name=f"{query} (Grade A)",
            supplier_name="深圳华强北电子 (Mock)",
            moq=100,
            price_tiers={"1+": round(base_price * 1.2, 4), "100+": round(base_price, 4), "1000+": round(base_price * 0.85, 4)},
            stock_status="in_stock",
            source_url="https://mock.szlcsc.com/product/001",
            unit_price_rmb=round(base_price * 0.85, 4),
            currency="CNY",
        ),
        SupplierQuote(
            component_name=f"{query} (Standard)",
            supplier_name="广州立创商城 (Mock)",
            moq=50,
            price_tiers={"1+": round(base_price * 1.3, 4), "50+": round(base_price * 1.05, 4), "500+": round(base_price * 0.9, 4)},
            stock_status="in_stock",
            source_url="https://mock.lcsc.com/product/002",
            unit_price_rmb=round(base_price * 0.9, 4),
            currency="CNY",
        ),
        SupplierQuote(
            component_name=f"{query} (Economy)",
            supplier_name="东莞元器件批发 (Mock)",
            moq=500,
            price_tiers={"500+": round(base_price * 0.75, 4), "5000+": round(base_price * 0.6, 4)},
            stock_status="low_stock",
            source_url="https://mock.dg-parts.com/product/003",
            unit_price_rmb=round(base_price * 0.6, 4),
            currency="CNY",
        ),
    ]


# ═══════════════════════════════════════════════════════════════
#  SupplyMiner — 幽灵矿工主类
# ═══════════════════════════════════════════════════════════════

class SupplyMiner:
    """幽灵矿工 — 供应链底价采集引擎

    工作流:
      1. StealthBrowser 打开目标 URL (反检测)
      2. WebCleaner 将 DOM 转为干净 Markdown
      3. LLMExtractor 从 Markdown 提取 Top-3 SupplierQuote
      4. 返回 MinerResult

    降级策略:
      - Playwright 不可用 → httpx 降级抓取
      - DeepSeek 不可用 → Ollama 本地模型
      - 全部失败 → mock 数据
      - 超时 15 秒 → 立即返回空结果
    """

    def __init__(self, settings: MinerSettings | None = None) -> None:
        self._settings = settings or MinerSettings()
        self._cleaner = WebCleaner()
        self._extractor = LLMExtractor(self._settings)

    async def mine(
        self,
        query: str,
        target_urls: list[str] | None = None,
    ) -> MinerResult:
        """执行供应链底价采集

        Parameters
        ----------
        query : str
            搜索查询 (如 "100nF 50V 0805 MLCC ceramic capacitor")
        target_urls : list[str] | None
            目标 URL 列表 (为空则使用默认搜索引擎)

        Returns
        -------
        MinerResult
            采集结果
        """
        import time
        start = time.time()

        # Mock 模式
        if self._settings.MINER_MODE == "mock":
            quotes = _generate_mock_quotes(query)
            elapsed = round(time.time() - start, 2)
            logger.info("幽灵矿工 (mock): query=%s quotes=%d elapsed=%.2fs", query[:40], len(quotes), elapsed)
            return MinerResult(
                query=query,
                quotes=quotes,
                source="mock",
                elapsed_sec=elapsed,
            )

        # Live 模式
        try:
            result = await asyncio.wait_for(
                self._mine_live(query, target_urls),
                timeout=self._settings.MINER_TIMEOUT_SEC,
            )
            result.elapsed_sec = round(time.time() - start, 2)
            return result
        except asyncio.TimeoutError:
            elapsed = round(time.time() - start, 2)
            logger.warning("幽灵矿工超时 (%ds): query=%s", self._settings.MINER_TIMEOUT_SEC, query[:40])
            return MinerResult(
                query=query,
                source="timeout",
                error=f"采集超时 ({self._settings.MINER_TIMEOUT_SEC}s)",
                elapsed_sec=elapsed,
            )
        except Exception as exc:
            elapsed = round(time.time() - start, 2)
            logger.error("幽灵矿工异常: %s", exc)
            return MinerResult(
                query=query,
                source="error",
                error=str(exc),
                elapsed_sec=elapsed,
            )

    async def _mine_live(
        self,
        query: str,
        target_urls: list[str] | None,
    ) -> MinerResult:
        """实际抓取流程"""
        urls = _normalize_target_urls(target_urls, query)
        if not urls:
            logger.warning("幽灵矿工: 无合法抓取 URL（查询为空或 URL 未通过安全校验）")
            quotes = _generate_mock_quotes(query)
            return MinerResult(query=query, quotes=quotes, source="mock_fallback")

        all_markdown = ""

        # 尝试 Playwright 抓取
        try:
            from rpa_engine.browser_stealth import StealthBrowser, RPASettings

            rpa_settings = RPASettings()
            if self._settings.PROXY_URL:
                rpa_settings.PROXY_SERVER = self._settings.PROXY_URL

            async with StealthBrowser(rpa_settings) as browser:
                for url in urls[:2]:  # 最多抓 2 个 URL（与 httpx 分支一致）
                    try:
                        page = await browser.new_page()
                        await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                        await browser.human_delay(1000, 3000)
                        html = await page.content()
                        await page.close()
                        md = self._cleaner.html_to_markdown(html)
                        all_markdown += f"\n\n## Source: {url}\n\n{md}"
                    except Exception as exc:
                        logger.warning("页面抓取失败 %s: %s", url[:50], exc)
                        continue
        except Exception as exc:
            logger.warning("Playwright 不可用，降级 httpx: %s", exc)
            all_markdown = await self._fallback_httpx(urls)

        if not all_markdown.strip():
            # 全部失败 → mock 降级
            logger.warning("所有抓取源失败，降级 mock")
            quotes = _generate_mock_quotes(query)
            return MinerResult(query=query, quotes=quotes, source="mock_fallback")

        # LLM 提取
        quotes = await self._extractor.extract_quotes(all_markdown, query)

        if not quotes:
            # LLM 提取失败 → mock 降级
            quotes = _generate_mock_quotes(query)
            return MinerResult(
                query=query, quotes=quotes, source="mock_fallback",
                raw_markdown=all_markdown[:2000],
            )

        return MinerResult(
            query=query, quotes=quotes, source="live",
            raw_markdown=all_markdown[:2000],
        )

    async def _fallback_httpx(self, urls: list[str]) -> str:
        """httpx 降级抓取"""
        try:
            import httpx

            all_md = ""
            async with httpx.AsyncClient(
                timeout=10,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0"},
            ) as client:
                for url in urls[:2]:
                    try:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        md = self._cleaner.html_to_markdown(resp.text)
                        all_md += f"\n\n## Source: {url}\n\n{md}"
                    except Exception as exc:
                        logger.debug("httpx 抓取失败 %s: %s", url[:50], exc)
            return all_md
        except Exception as exc:
            logger.debug("httpx 降级失败: %s", exc)
            return ""


# ═══════════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════════

_miner: SupplyMiner | None = None


def get_supply_miner() -> SupplyMiner:
    """获取全局幽灵矿工单例"""
    global _miner
    if _miner is None:
        _miner = SupplyMiner()
    return _miner
