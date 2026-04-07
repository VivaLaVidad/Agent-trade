"""
test_supply_miner.py — 幽灵矿工 + SupplyChainScout 节点测试
═══════════════════════════════════════════════════════════════
覆盖:
  1. SupplierQuote Pydantic 模型验证
  2. WebCleaner HTML → Markdown 转换
  3. SupplyMiner mock 模式采集
  4. SupplyMiner 超时处理
  5. supply_scout_node 本地有候选 → 不触发
  6. supply_scout_node 本地无候选 → 触发 mock 矿工
  7. supply_scout_node 降级消息验证
  8. matching_graph 含 scout 节点编译
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))


def test_supplier_quote_model() -> None:
    """SupplierQuote Pydantic 模型验证"""
    from rpa_engine.supply_miner import SupplierQuote

    q = SupplierQuote(
        component_name="100nF 50V 0805 MLCC",
        supplier_name="Test Supplier",
        moq=100,
        price_tiers={"1+": 0.5, "100+": 0.4, "1000+": 0.35},
        stock_status="in_stock",
        unit_price_rmb=0.35,
    )
    assert q.component_name == "100nF 50V 0805 MLCC"
    assert q.moq == 100
    assert q.price_tiers["1000+"] == 0.35
    assert q.unit_price_rmb == 0.35
    assert q.scraped_at  # 自动填充


def test_supplier_quote_defaults() -> None:
    """SupplierQuote 默认值"""
    from rpa_engine.supply_miner import SupplierQuote

    q = SupplierQuote(component_name="Test", supplier_name="S1")
    assert q.moq == 1
    assert q.stock_status == "unknown"
    assert q.currency == "CNY"
    assert q.unit_price_rmb == 0.0


def test_web_cleaner_html_to_markdown() -> None:
    """WebCleaner HTML → Markdown 转换"""
    from rpa_engine.supply_miner import WebCleaner

    html = """
    <html>
    <head><style>body { color: red; }</style></head>
    <body>
        <script>alert('xss')</script>
        <nav>Navigation</nav>
        <div class="content">
            <h1>Product: 100nF Capacitor</h1>
            <p>Price: ¥0.50/pc</p>
            <p>MOQ: 100 pcs</p>
            <table>
                <tr><th>Qty</th><th>Price</th></tr>
                <tr><td>1+</td><td>¥0.50</td></tr>
                <tr><td>100+</td><td>¥0.40</td></tr>
            </table>
        </div>
        <footer>Footer content</footer>
    </body>
    </html>
    """
    md = WebCleaner.html_to_markdown(html)

    # 应包含产品信息
    assert "100nF Capacitor" in md or "capacitor" in md.lower()
    assert "0.50" in md or "Price" in md

    # 不应包含 JS/CSS/导航/页脚
    assert "alert" not in md
    assert "color: red" not in md


def test_web_cleaner_fallback() -> None:
    """WebCleaner 降级清洗"""
    from rpa_engine.supply_miner import WebCleaner

    html = "<p>Simple <b>text</b> with <script>js</script> tags</p>"
    result = WebCleaner._fallback_clean(html)
    assert "Simple" in result
    assert "text" in result
    assert "<script>" not in result


def test_safe_fetch_url_allows_public_https() -> None:
    """live 抓取 URL 白名单：公网 https 通过"""
    from rpa_engine.supply_miner import _is_safe_fetch_url

    assert _is_safe_fetch_url("https://www.szlcsc.com/so/test/catalog.html") is True
    assert _is_safe_fetch_url("http://example.com/path?q=1") is True


def test_safe_fetch_url_blocks_ssrf_vectors() -> None:
    """拒绝 file://、内网与 loopback，降低 SSRF 面"""
    from rpa_engine.supply_miner import _is_safe_fetch_url

    assert _is_safe_fetch_url("file:///etc/passwd") is False
    assert _is_safe_fetch_url("http://127.0.0.1:8080/") is False
    assert _is_safe_fetch_url("http://192.168.1.1/") is False
    assert _is_safe_fetch_url("http://10.0.0.1/") is False
    assert _is_safe_fetch_url("http://localhost/foo") is False
    assert _is_safe_fetch_url("ftp://example.com/") is False


def test_normalize_target_urls_encodes_query() -> None:
    """默认 LCSC 搜索链接应对 query 做 URL 编码"""
    from rpa_engine.supply_miner import _normalize_target_urls

    urls = _normalize_target_urls(None, "100nF 50V/MLCC")
    assert len(urls) == 1
    assert urls[0].startswith("https://")
    assert "%2F" in urls[0] or "catalog.html" in urls[0]


def test_normalize_target_urls_filters_unsafe_override() -> None:
    """用户传入的 target_urls 经安全过滤，全不合格时返回空列表"""
    from rpa_engine.supply_miner import _normalize_target_urls

    assert _normalize_target_urls(["http://127.0.0.1/hook"], "x") == []


def test_llm_extractor_parse_quotes_wrapped_object() -> None:
    """DeepSeek json_object 形态：{{\"quotes\": [...]}}"""
    from rpa_engine.supply_miner import LLMExtractor

    raw = (
        '{"quotes":[{"component_name":"Cap","supplier_name":"S","moq":10,'
        '"unit_price_rmb":0.5}]}'
    )
    qs = LLMExtractor._parse_response(raw)
    assert len(qs) == 1
    assert qs[0].component_name == "Cap"
    assert qs[0].supplier_name == "S"


def test_supply_miner_mock_mode() -> None:
    """SupplyMiner mock 模式采集"""
    from rpa_engine.supply_miner import SupplyMiner, MinerSettings

    async def _run() -> None:
        settings = MinerSettings(MINER_MODE="mock")
        miner = SupplyMiner(settings)
        result = await miner.mine("100nF 50V 0805 MLCC ceramic capacitor")

        assert result.source == "mock"
        assert len(result.quotes) == 3
        assert result.error == ""
        assert result.elapsed_sec >= 0

        # 验证报价结构
        for q in result.quotes:
            assert q.component_name
            assert q.supplier_name
            assert q.moq > 0
            assert q.unit_price_rmb > 0
            assert len(q.price_tiers) > 0

    asyncio.run(_run())


def test_supply_miner_mock_deterministic() -> None:
    """SupplyMiner mock 模式确定性 (同一查询返回相同结果)"""
    from rpa_engine.supply_miner import SupplyMiner, MinerSettings

    async def _run() -> None:
        settings = MinerSettings(MINER_MODE="mock")
        miner = SupplyMiner(settings)

        r1 = await miner.mine("10K resistor 0603")
        r2 = await miner.mine("10K resistor 0603")

        assert len(r1.quotes) == len(r2.quotes)
        for q1, q2 in zip(r1.quotes, r2.quotes):
            assert q1.unit_price_rmb == q2.unit_price_rmb
            assert q1.supplier_name == q2.supplier_name

    asyncio.run(_run())


def test_supply_miner_different_queries() -> None:
    """不同查询返回不同价格"""
    from rpa_engine.supply_miner import SupplyMiner, MinerSettings

    async def _run() -> None:
        settings = MinerSettings(MINER_MODE="mock")
        miner = SupplyMiner(settings)

        r1 = await miner.mine("100nF capacitor")
        r2 = await miner.mine("STM32F103 MCU")

        # 不同查询应有不同价格
        prices1 = [q.unit_price_rmb for q in r1.quotes]
        prices2 = [q.unit_price_rmb for q in r2.quotes]
        assert prices1 != prices2

    asyncio.run(_run())


def test_supply_scout_node_not_triggered() -> None:
    """本地有候选 → scout 不触发"""
    from modules.supply_chain.matching_graph import supply_scout_node

    state = {
        "candidates": [{"sku_id": "test-001", "sku_name": "Test SKU"}],
        "structured_demand": {"category": "capacitor"},
        "status": "candidates_found",
    }
    result = supply_scout_node(state)
    assert result["scout_result"]["triggered"] is False


def test_supply_scout_node_triggered_mock() -> None:
    """本地无候选 → scout 触发 mock 矿工"""
    # 确保 mock 模式
    os.environ["MINER_MODE"] = "mock"

    from modules.supply_chain.matching_graph import supply_scout_node

    state = {
        "candidates": [],
        "structured_demand": {
            "category": "capacitor",
            "product_keywords": "100nF 50V MLCC",
        },
        "status": "no_candidates_local",
    }
    result = supply_scout_node(state)

    assert result.get("scout_result", {}).get("triggered") is True
    assert result.get("status") == "candidates_found"
    assert len(result.get("candidates", [])) > 0

    # 验证候选格式
    for c in result["candidates"]:
        assert "sku_id" in c
        assert "sku_name" in c
        assert "unit_price_rmb" in c
        assert c.get("source") == "ghost_miner"


def test_supply_scout_degradation_message() -> None:
    """scout 降级消息验证"""
    from modules.supply_chain.matching_graph import _SCOUT_DEGRADATION_MSG

    assert "特殊缺货件" in _SCOUT_DEGRADATION_MSG
    assert "2 小时" in _SCOUT_DEGRADATION_MSG


def test_matching_graph_compiles_with_scout() -> None:
    """matching_graph 含 scout 节点后仍能正常编译"""
    from modules.supply_chain.matching_graph import build_matching_graph
    graph = build_matching_graph()
    assert graph is not None
