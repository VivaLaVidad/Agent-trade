"""
control_panel.py — Project Claw 工业级本地暗箱控制面板
─────────────────────────────────────────────────────
PyQt6 深色极客风格桌面客户端（全中文界面）：
  左栏：实时任务流表（时间戳 / 模块 / 动作 / 状态）
  右栏：系统指标 + 引擎启停 + 供应链撮合演示按钮
"""

import asyncio
import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from dotenv import load_dotenv
load_dotenv()

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QFont, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import psutil
from core.engine import MainEngine

_MONO_FONT = "Consolas" if os.name == "nt" else "Menlo"
_REFRESH_MS = 2000

_DEMO_BUYER_REQUEST = """\
Hi, I am Ahmed from Cairo, Egypt. I need 500pcs SMD ceramic capacitors, \
100nF 50V, 0805 package. Must have CE certification. \
Budget is USD 200. Please quote CIF Cairo. Urgent delivery needed.\
"""


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(18, 18, 24))
    p.setColor(QPalette.ColorRole.WindowText, QColor(200, 210, 220))
    p.setColor(QPalette.ColorRole.Base, QColor(25, 25, 35))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(30, 30, 42))
    p.setColor(QPalette.ColorRole.Text, QColor(200, 210, 220))
    p.setColor(QPalette.ColorRole.Button, QColor(35, 35, 50))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(200, 210, 220))
    p.setColor(QPalette.ColorRole.Highlight, QColor(0, 150, 136))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    return p


_GLOBAL_STYLE = """
QMainWindow { background-color: #121218; }
QTableWidget {
    background-color: #191923;
    gridline-color: #2a2a3a;
    border: 1px solid #2a2a3a;
    font-family: %(mono)s;
    font-size: 12px;
}
QTableWidget::item { padding: 4px 8px; }
QHeaderView::section {
    background-color: #1e1e2e;
    color: #8899aa;
    border: 1px solid #2a2a3a;
    padding: 6px;
    font-weight: bold;
}
QLabel { color: #c8d2dc; }
QProgressBar {
    background-color: #1e1e2e;
    border: 1px solid #2a2a3a;
    border-radius: 4px;
    text-align: center;
    color: #c8d2dc;
    font-size: 11px;
}
QProgressBar::chunk { background-color: #009688; border-radius: 3px; }
QPushButton {
    border: 2px solid #2a2a3a;
    border-radius: 6px;
    padding: 12px 24px;
    font-size: 14px;
    font-weight: bold;
    font-family: %(mono)s;
}
QPushButton:hover { border-color: #009688; }
QPushButton:pressed { background-color: #0d0d14; }
QPushButton:disabled { color: #555566; border-color: #222233; }
""" % {"mono": _MONO_FONT}


class LogSignal(QObject):
    """线程安全的日志信号桥"""
    log = pyqtSignal(str, str, str, str)


class MetricCard(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame { background-color: #1e1e2e; border: 1px solid #2a2a3a; "
            "border-radius: 6px; padding: 8px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self._title = QLabel(title)
        self._title.setFont(QFont(_MONO_FONT, 10))
        self._title.setStyleSheet("color: #667788;")
        self._value = QLabel("--")
        self._value.setFont(QFont(_MONO_FONT, 22, QFont.Weight.Bold))
        self._value.setStyleSheet("color: #00e5cc;")
        layout.addWidget(self._title)
        layout.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text)


class ControlPanel(QMainWindow):

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Project Claw — 暗箱自动化控制中心")
        self.setMinimumSize(1280, 750)

        self._engine = MainEngine()
        self._demo_running = False

        self._log_signal = LogSignal()
        self._log_signal.log.connect(self._add_log)

        self._build_ui()
        self._setup_timers()
        self._add_log("系统", "控制面板", "初始化完成", "success")

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        root.addWidget(self._build_left_pane(), stretch=7)
        root.addWidget(self._build_right_pane(), stretch=3)

    def _build_left_pane(self) -> QWidget:
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("实时任务流")
        header.setFont(QFont(_MONO_FONT, 13, QFont.Weight.Bold))
        header.setStyleSheet("color: #009688; padding: 4px;")
        layout.addWidget(header)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["时间", "模块", "操作", "状态"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)
        return pane

    def _build_right_pane(self) -> QWidget:
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        header = QLabel("系统运行指标")
        header.setFont(QFont(_MONO_FONT, 13, QFont.Weight.Bold))
        header.setStyleSheet("color: #009688; padding: 4px;")
        layout.addWidget(header)

        self._cpu_card = MetricCard("处理器占用")
        self._mem_card = MetricCard("内存占用")
        self._uptime_card = MetricCard("运行时长")
        layout.addWidget(self._cpu_card)
        layout.addWidget(self._mem_card)
        layout.addWidget(self._uptime_card)

        queue_label = QLabel("任务队列")
        queue_label.setFont(QFont(_MONO_FONT, 10))
        queue_label.setStyleSheet("color: #667788; margin-top: 8px;")
        layout.addWidget(queue_label)
        self._queue_bar = QProgressBar()
        self._queue_bar.setRange(0, 100)
        self._queue_bar.setValue(0)
        self._queue_bar.setFormat("%v 个待处理")
        layout.addWidget(self._queue_bar)

        layout.addStretch()

        sep = QLabel("─── 供应链撮合 ───")
        sep.setFont(QFont(_MONO_FONT, 10))
        sep.setStyleSheet("color: #445566;")
        sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sep)

        self._demo_btn = QPushButton("供应链撮合演示")
        self._demo_btn.setStyleSheet(
            "QPushButton { background-color: #1a1a3d; color: #7b8cff; border-color: #5566dd; }"
            "QPushButton:hover { background-color: #252550; }"
        )
        self._demo_btn.setFixedHeight(48)
        self._demo_btn.clicked.connect(self._on_demo)
        layout.addWidget(self._demo_btn)

        sep2 = QLabel("─── 引擎控制 ───")
        sep2.setFont(QFont(_MONO_FONT, 10))
        sep2.setStyleSheet("color: #445566;")
        sep2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(sep2)

        self._start_btn = QPushButton("启  动  引  擎")
        self._start_btn.setStyleSheet(
            "QPushButton { background-color: #0a3d2e; color: #00e5cc; border-color: #00e5cc; }"
            "QPushButton:hover { background-color: #0d5940; }"
        )
        self._start_btn.setFixedHeight(48)
        self._start_btn.clicked.connect(self._on_start)
        layout.addWidget(self._start_btn)

        self._stop_btn = QPushButton("停  止  引  擎")
        self._stop_btn.setStyleSheet(
            "QPushButton { background-color: #3d0a0a; color: #ff5555; border-color: #ff5555; }"
            "QPushButton:hover { background-color: #591010; }"
        )
        self._stop_btn.setFixedHeight(48)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        layout.addWidget(self._stop_btn)

        return pane

    def _setup_timers(self) -> None:
        self._metrics_timer = QTimer(self)
        self._metrics_timer.timeout.connect(self._refresh_metrics)
        self._metrics_timer.start(_REFRESH_MS)

    def _refresh_metrics(self) -> None:
        try:
            metrics = self._engine.get_metrics()
            status = self._engine.status()
            self._cpu_card.set_value(f"{metrics['cpu_percent']:.1f}%")
            self._mem_card.set_value(f"{metrics['memory_mb']:.0f} MB")
            if status["running"]:
                secs = int(status["uptime_seconds"])
                mins, s = divmod(secs, 60)
                hrs, m = divmod(mins, 60)
                self._uptime_card.set_value(f"{hrs:02d}:{m:02d}:{s:02d}")
            else:
                self._uptime_card.set_value("离线")
            self._queue_bar.setValue(metrics.get("pending_tasks", 0))
            self._start_btn.setEnabled(not status["running"])
            self._stop_btn.setEnabled(status["running"])
        except Exception:
            pass

    def _on_start(self) -> None:
        result = self._engine.start()
        self._add_log("引擎", "启动",
                       f"API={result.get('api_pid','?')} RPA={result.get('rpa_pid','?')}", "success")
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

    def _on_stop(self) -> None:
        result = self._engine.stop()
        self._add_log("引擎", "停止",
                       f"清理: {result.get('killed_pids',[]) or '无残留'}", "stopped")
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    # ═══════════════════════════════════════════════════════════
    #  供应链撮合演示（后台线程运行，通过信号刷新 UI）
    # ═══════════════════════════════════════════════════════════
    def _on_demo(self) -> None:
        if self._demo_running:
            return
        self._demo_running = True
        self._demo_btn.setEnabled(False)
        self._demo_btn.setText("撮合运行中...")
        thread = threading.Thread(target=self._run_demo_thread, daemon=True)
        thread.start()

    def _emit(self, module: str, action: str, detail: str, status: str) -> None:
        self._log_signal.log.emit(module, action, detail, status)

    def _run_demo_thread(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_demo_async())
        except Exception as exc:
            self._emit("演示", "异常", str(exc)[:80], "failed")
        finally:
            self._demo_running = False
            self._demo_btn.setEnabled(True)
            self._demo_btn.setText("供应链撮合演示")

    async def _run_demo_async(self) -> None:
        self._emit("演示", "开始", "初始化供应商数据库...", "running")

        import modules.supply_chain.models  # noqa
        from database.models import Base, async_engine, AsyncSessionFactory

        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from modules.supply_chain.mock_data import generate_mock_catalog
        stats = await generate_mock_catalog(num_suppliers=50, num_skus=200)
        self._emit("数据库", "初始化",
                    f"{stats['suppliers']}家供应商 / {stats['skus']}个SKU", "success")

        self._emit("C端买家", "发送询盘",
                    "500pcs 100nF/50V/0805 电容 → 开罗 $200", "running")

        from modules.supply_chain.demand_agent import DemandAgent
        demand_agent = DemandAgent()
        demand = await demand_agent.execute(None, {"raw_input": _DEMO_BUYER_REQUEST})

        if not demand.get("valid"):
            self._emit("C端买家", "需求解析失败", demand.get("error", ""), "failed")
            return

        self._emit("C端需求", "解析完成",
                    f"{demand.get('category','')} qty={demand.get('quantity',0)} ${demand.get('budget_usd',0)}",
                    "success")

        from modules.supply_chain.supply_agent import SupplyAgent
        self._emit("B端供应链", "检索中", f"品类={demand.get('category','')}", "running")
        supply_agent = SupplyAgent()
        candidates = await supply_agent.execute(None, {
            "category": demand.get("category", ""),
            "specs": demand.get("specs", {}),
            "certs_required": demand.get("certs_required", []),
            "budget_usd": demand.get("budget_usd", 0),
            "quantity": demand.get("quantity", 0),
            "top_n": 5,
        })

        if not candidates:
            self._emit("B端供应链", "无匹配", "未找到候选SKU", "failed")
            return

        from agents.agent_workflow import apply_risk_defense_to_candidates
        apply_risk_defense_to_candidates(candidates)
        self._emit("风险防御", "节点就绪", "RAG 后：价格波动熔断 + 库存 Agent 校验", "running")

        for c in candidates:
            if c.get("abnormal_quote_risk"):
                dev = c.get("price_deviation_vs_hist_pct", 0)
                self._emit(
                    "风险防御",
                    "异常报价风险",
                    f"{c.get('sku_name','')[:40]} 标价偏离历史均价 {dev}% — PriceVolatilityMonitor 已二次确认",
                    "risk_volatility",
                )
            if c.get("inventory_low_stock"):
                vq = c.get("inventory_verified_qty", 0)
                self._emit(
                    "风险防御",
                    "库存防错",
                    f"{c.get('sku_name','')[:40]} 核实 {vq}pcs — 报价须含「库存紧缺，请限期确认」",
                    "risk_inventory",
                )

        for i, c in enumerate(candidates[:3], 1):
            self._emit("B端供应链", f"候选#{i}",
                        f"{c.get('sku_name','')} ¥{c.get('unit_price_rmb',0)} MOQ={c.get('moq',0)}",
                        "success")

        from modules.supply_chain.negotiator import NegotiatorAgent
        self._emit("谈判引擎", "启动决策树", "MOQ/认证/预算/贸易术语", "running")
        negotiator = NegotiatorAgent()
        neg_result = await negotiator.execute(None, demand, candidates)

        for line in neg_result.get("negotiation_log", [])[:5]:
            tag = "success" if "[OK]" in line else "running" if "[MOQ]" in line else "failed"
            self._emit("谈判", "决策", line, tag)

        best = neg_result.get("best_match")
        if best and best.get("status") == "approved":
            amount = best.get("landed_usd", 0)
            self._emit("谈判引擎", "撮合成功",
                        f"{best.get('sku_name','')} ${amount} {best.get('shipping_term','')}",
                        "success")

            from modules.supply_chain.ledger import LedgerService
            ledger = LedgerService(fee_rate=0.01)
            txn = ledger.create_transaction(
                merchant_id="merchant-demo-001",
                client_id="client-demo-buyer",
                amount_usd=amount,
                match_id=best.get("sku_id", ""),
                po_number=f"PO-{uuid.uuid4().hex[:6].upper()}",
            )
            await ledger.persist(txn)

            self._emit("账本", "路由费扣费",
                        f"成交 ${txn['amount_usd']:.2f} → 路由费 ${txn['routing_fee_usd']:.2f} (1%)",
                        "fee")
            self._emit("账本", "数字签名",
                        f"txn={txn['transaction_id'][:12]}… sig={txn['signature'][:16]}…",
                        "fee")

            self._emit("订单生成", "AI生成PO中", "调用 qwen3:4b...", "running")
            from modules.supply_chain.matching_graph import MatchingOrchestrator
            orch = MatchingOrchestrator()
            result = await orch.run(_DEMO_BUYER_REQUEST, thread_id=f"demo-{uuid.uuid4().hex[:8]}")
            po = result.get("purchase_order", {})
            if po.get("po_number"):
                self._emit("订单生成", f"PO#{po['po_number']}",
                            f"${po.get('total_usd',0)} {po.get('shipping_term','')}",
                            "success")
            else:
                self._emit("订单生成", "完成", "订单已生成", "success")
        else:
            bundling = neg_result.get("bundling_suggestions", [])
            alts = neg_result.get("alternatives", [])
            if bundling:
                self._emit("谈判引擎", "拼单建议",
                            f"{len(bundling)}个SKU需拼单达到MOQ", "running")
            if alts:
                self._emit("谈判引擎", "替代方案",
                            f"{len(alts)}个替代供应商", "running")
            self._emit("谈判引擎", "本轮未成交",
                        "已生成拼单/替代建议供人工审核", "stopped")

        self._emit("演示", "结束", "供应链撮合全流程完成", "success")

    def _add_log(self, module: str, action: str, detail: str, status: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        row = self._table.rowCount()
        self._table.insertRow(row)

        status_labels = {
            "success": "成功", "running": "运行中",
            "failed": "失败", "stopped": "已停止",
            "fee": "扣费流水",
            "risk_volatility": "异常报价风险",
            "risk_inventory": "库存防错",
        }
        display_status = status_labels.get(status, status)

        colors = {
            "success": QColor(0, 229, 204),
            "running": QColor(255, 193, 7),
            "failed": QColor(255, 85, 85),
            "stopped": QColor(150, 150, 170),
            "fee": QColor(255, 215, 0),
            "risk_volatility": QColor(255, 152, 0),
            "risk_inventory": QColor(255, 213, 79),
        }
        color = colors.get(status, QColor(200, 210, 220))

        items = [ts, module, f"{action}: {detail}", display_status]
        for col, text in enumerate(items):
            item = QTableWidgetItem(text)
            item.setFont(QFont(_MONO_FONT, 11))
            if col == 3:
                item.setForeground(color)
            self._table.setItem(row, col, item)

        self._table.scrollToBottom()

    def closeEvent(self, event) -> None:
        if self._engine.is_running:
            self._add_log("系统", "优雅停机", "正在关闭所有子进程...", "running")
            self._engine.stop()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(_GLOBAL_STYLE)
    window = ControlPanel()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
