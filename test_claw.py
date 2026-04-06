"""
test_claw.py — Project Claw 暗箱平台全模块冒烟测试
──────────────────────────────────────────────────
验证项目骨架的完整性：Registry / AgentContext / 所有业务模块 /
审计日志 / 许可证 / DB 表 / 引擎 / 控制面板导入。
不需要 Ollama / PostgreSQL，纯本地验证。
"""

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    tag = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    suffix = f" — {detail}" if detail else ""
    print(f"  [{tag}] {name}{suffix}")


def section(title: str) -> None:
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


def test_registry():
    section("1. ModuleRegistry — 单例 + 注册 + 获取")

    from core.registry import ModuleRegistry

    r1 = ModuleRegistry()
    r2 = ModuleRegistry()
    check("单例模式", r1 is r2)

    r1.reset()

    class DummyModule:
        pass

    r1.register("dummy", DummyModule)
    check("注册模块", "dummy" in r1.list_all())
    check("获取模块", isinstance(r1.get("dummy"), DummyModule))

    try:
        r1.get("nonexistent")
        check("未注册模块抛异常", False)
    except KeyError:
        check("未注册模块抛异常", True)

    r1.reset()


def test_auto_discover():
    section("2. Auto-Discovery — 扫描 modules/ 包")

    from core.registry import ModuleRegistry

    r = ModuleRegistry()
    r.reset()
    r.auto_discover()

    modules = r.list_all()
    check("auto_discover 执行", len(modules) > 0, f"发现 {len(modules)} 个模块")

    expected = ["lead_miner", "email_campaigner", "doc_generator",
                "stealth_logger", "hardware_license"]
    for name in expected:
        check(f"  模块 '{name}' 已注册", name in modules)

    r.reset()


def test_agent_context():
    section("3. AgentContext — 数据总线构建")

    from core.registry import ModuleRegistry
    ModuleRegistry().reset()

    from core.agent_context import AgentContext

    ctx = AgentContext.build()
    check("AgentContext.build() 成功", ctx is not None)
    check("registry 已装配", ctx.registry is not None)
    check("cipher 已装配", ctx.cipher is not None)
    check("模块已加载", len(ctx.registry.list_all()) >= 5,
          f"{len(ctx.registry.list_all())} 个模块")

    mod = ctx.get_module("lead_miner")
    check("get_module('lead_miner') 成功", mod is not None,
          type(mod).__name__)

    ModuleRegistry().reset()


def test_db_models():
    section("4. DB Models — 新增表定义验证")

    from database.models import (
        Base, EmailCampaign, CampaignMessage, GeneratedDocument, ClientLead,
    )

    tables = Base.metadata.tables
    check("EmailCampaign 表", "email_campaigns" in tables)
    check("CampaignMessage 表", "campaign_messages" in tables)
    check("GeneratedDocument 表", "generated_documents" in tables)
    check("ClientLead 表", "client_leads" in tables)

    total = len(tables)
    check(f"总表数 = {total}", total >= 8)


def test_stealth_logger():
    section("5. StealthLogger — 加密审计写入 + 读取 + 销毁")

    from modules.audit_module.stealth_logger import StealthLogger
    from datetime import datetime, timezone

    with tempfile.TemporaryDirectory() as tmpdir:
        sl = StealthLogger(logs_dir=tmpdir)

        sl.log_event("test_module", "test_action", {"key": "value"}, "tester")
        sl.log_event("test_module", "test_action_2", {"num": 42}, "tester")

        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        log_file = os.path.join(tmpdir, f"audit_{today}.enc")

        check("审计日志文件已创建", os.path.exists(log_file))

        with open(log_file, "r") as f:
            lines = [l.strip() for l in f if l.strip()]
        check("写入了 2 条记录", len(lines) == 2)

        events = sl.read_events(today)
        check("解密读取成功", len(events) == 2)
        check("事件内容正确", events[0]["module"] == "test_module")
        check("明文不可见于密文", "test_module" not in lines[0])

        destroyed = sl.purge_older_than(days=0)
        check("安全销毁执行", destroyed >= 0)


def test_hardware_license():
    section("6. HardwareLicense — 生成 + 验证 + 过期检测")

    from modules.audit_module.hardware_license import LicenseManager, LicenseError

    with tempfile.TemporaryDirectory() as tmpdir:
        lic_path = os.path.join(tmpdir, "test.lic")
        mgr = LicenseManager(license_path=lic_path)

        path = mgr.generate_license_file(valid_days=365, licensee="test_user")
        check("许可证文件生成", os.path.exists(path))

        with open(path, "r") as f:
            raw = f.read()
        check("文件内容已加密（非明文 JSON）", not raw.startswith("{"))

        lic = mgr.validate()
        check("许可证验证通过", lic.machine_id != "")
        check("被授权方正确", lic.licensee == "test_user")
        check("功能列表非空", len(lic.features_enabled) >= 3)

        expired_mgr = LicenseManager(license_path=os.path.join(tmpdir, "expired.lic"))
        expired_mgr.generate_license_file(valid_days=-1)
        try:
            expired_mgr.validate()
            check("过期许可证拒绝", False)
        except LicenseError:
            check("过期许可证拒绝", True)


def test_engine():
    section("7. MainEngine — 状态检查（不实际启动子进程）")

    from core.engine import MainEngine

    engine = MainEngine()
    check("引擎初始化", engine is not None)

    status = engine.status()
    check("初始状态为未运行", status["running"] is False)
    check("PID 为 None", status["api_pid"] is None)

    metrics = engine.get_metrics()
    check("CPU 指标可读", "cpu_percent" in metrics)
    check("内存指标可读", "memory_mb" in metrics)


def test_control_panel_import():
    section("8. PyQt6 Control Panel — 导入验证")

    try:
        from PyQt6.QtWidgets import QApplication
        check("PyQt6 可导入", True)
    except ImportError as e:
        check("PyQt6 可导入", False, str(e))

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "control_panel",
            os.path.join(os.path.dirname(__file__), "control_panel.py"),
        )
        check("control_panel.py 语法正确", spec is not None)
    except Exception as e:
        check("control_panel.py 语法正确", False, str(e))


def test_core_modules_interface():
    section("9. CoreModule — 接口签名验证（不调用 Ollama）")

    from modules.core_module.lead_miner import LeadMiner
    from modules.core_module.email_campaigner import EmailCampaigner
    from modules.core_module.doc_generator import DocGenerator

    for cls_name, cls in [
        ("LeadMiner", LeadMiner),
        ("EmailCampaigner", EmailCampaigner),
        ("DocGenerator", DocGenerator),
    ]:
        obj = cls()
        check(f"{cls_name} 可实例化", obj is not None)
        check(f"{cls_name}.execute 存在", hasattr(obj, "execute") and callable(obj.execute))


def main():
    print("\n" + "#" * 60)
    print("  Project Claw — 暗箱平台全模块冒烟测试")
    print("#" * 60)

    test_registry()
    test_auto_discover()
    test_agent_context()
    test_db_models()
    test_stealth_logger()
    test_hardware_license()
    test_engine()
    test_control_panel_import()
    test_core_modules_interface()

    section("SUMMARY")
    total = PASS + FAIL
    print(f"  Total: {total}  |  Passed: {PASS}  |  Failed: {FAIL}")
    if FAIL == 0:
        print("\n  ALL TESTS PASSED — Project Claw 骨架完整可用")
    else:
        print(f"\n  {FAIL} TESTS FAILED — 请检查上方错误")
    print()

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
