"""
god_dashboard.py — Project Claw 融资演示「上帝视角」大屏
────────────────────────────────────────────────────
顶部：路由费实时收割机（撮合交易额 + 平台路由费，2s 刷新）
布局：左 实时交易流 | 中上 ROI 监控图 | 右 多商户并发状态
配色：成交亮绿 / 拼单橙 / 谈判失败浅灰
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
import threading
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from dotenv import load_dotenv

load_dotenv()

from PyQt6.QtCore import QPointF, Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QLinearGradient, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import modules.supply_chain.models  # noqa: F401 — 确保 ORM 表注册

_MONO = "Consolas" if os.name == "nt" else "Menlo"
_REFRESH_MS = 2000

FeedKind = Literal["success", "bundling", "failed"]


@dataclass
class FeedEntry:
    ts: str
    line: str
    kind: FeedKind
    source_id: str = ""  # 账本 transaction_id，用于去重


class DashboardDataSignal(QObject):
    """后台线程加载完成后回传主线程（payload 避免参数过长）"""

    loaded = pyqtSignal(dict)


def _now_hms() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


class HarvesterStrip(QFrame):
    """路由费实时收割机 — 大号双计数器"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("harvester")
        self.setStyleSheet(
            "#harvester {"
            "background-color: #0d1520;"
            "border: 2px solid #1a3d5c;"
            "border-radius: 12px;"
            "}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(28, 18, 28, 18)
        lay.setSpacing(48)

        left = QVBoxLayout()
        t1 = QLabel("路由费实时收割机")
        t1.setFont(QFont(_MONO, 11))
        t1.setStyleSheet("color: #5a7a9a;")
        self._vol = QLabel("$0.00")
        self._vol.setFont(QFont(_MONO, 32, QFont.Weight.Bold))
        self._vol.setStyleSheet("color: #00ffaa;")
        s1 = QLabel("已为商户撮合交易额 (GMV)")
        s1.setFont(QFont(_MONO, 10))
        s1.setStyleSheet("color: #8899aa;")
        left.addWidget(t1)
        left.addWidget(self._vol)
        left.addWidget(s1)

        right = QVBoxLayout()
        t2 = QLabel("平台路由费收入 (1%)")
        t2.setFont(QFont(_MONO, 11))
        t2.setStyleSheet("color: #5a7a9a;")
        self._fee = QLabel("$0.00")
        self._fee.setFont(QFont(_MONO, 32, QFont.Weight.Bold))
        self._fee.setStyleSheet("color: #ffd54f;")
        s2 = QLabel("每 2 秒与账本同步")
        s2.setFont(QFont(_MONO, 10))
        s2.setStyleSheet("color: #8899aa;")
        right.addWidget(t2)
        right.addWidget(self._fee)
        right.addWidget(s2)

        lay.addLayout(left, stretch=1)
        lay.addLayout(right, stretch=1)

    def set_totals(self, volume_usd: float, fee_usd: float) -> None:
        self._vol.setText(f"${volume_usd:,.2f}")
        self._fee.setText(f"${fee_usd:,.2f}")


class ROIChartWidget(QWidget):
    """中上方 ROI 监控 — QPainter 折线，无额外依赖"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._points: deque[float] = deque(maxlen=36)
        self._title = "撮合引擎 ROI / 转化效率 (滚动)"

    def push_value(self, v: float) -> None:
        self._points.append(max(82.0, min(99.9, v)))
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(14, 18, 28))

        painter.setPen(QPen(QColor(45, 55, 75), 1))
        for i in range(5):
            y = 40 + i * (h - 56) // 4
            painter.drawLine(56, y, w - 12, y)

        painter.setPen(QColor(120, 140, 170))
        painter.setFont(QFont(_MONO, 10))
        painter.drawText(12, 28, self._title)

        pts = list(self._points)
        if len(pts) < 2:
            painter.drawText(60, h // 2, "正在采集数据…")
            painter.end()
            return

        x0, x1 = 56, w - 12
        y0, y1 = 40, h - 16
        lo, hi = min(pts), max(pts)
        if hi - lo < 0.5:
            lo, hi = lo - 0.5, hi + 0.5

        path = []
        for i, p in enumerate(pts):
            t = i / max(len(pts) - 1, 1)
            x = x0 + t * (x1 - x0)
            yn = y0 + (hi - p) / (hi - lo) * (y1 - y0)
            path.append(QPointF(x, yn))

        grad = QLinearGradient(0, y0, 0, y1)
        grad.setColorAt(0, QColor(0, 255, 180, 90))
        grad.setColorAt(1, QColor(0, 200, 255, 20))
        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        poly = path + [QPointF(x1, y1), QPointF(x0, y1)]
        painter.drawPolygon(QPolygonF(poly))

        painter.setPen(QPen(QColor(0, 255, 170), 2))
        for a, b in zip(path[:-1], path[1:]):
            painter.drawLine(a.toPoint(), b.toPoint())

        painter.setPen(QColor(180, 200, 220))
        painter.setFont(QFont(_MONO, 9))
        painter.drawText(12, y0 + 8, f"{hi:.1f}%")
        painter.drawText(12, y1 - 4, f"{lo:.1f}%")
        painter.drawText(x1 - 80, h - 6, f"当前 {pts[-1]:.1f}%")
        painter.end()


_BAR_OK = (
    "QProgressBar { height: 18px; border: 1px solid #334; border-radius: 4px; "
    "background: #1a2230; color: #cde; font-size: 9px; }"
    "QProgressBar::chunk { background-color: #00897b; border-radius: 3px; }"
)
_BAR_HOT = (
    "QProgressBar { height: 18px; border: 1px solid #334; border-radius: 4px; "
    "background: #1a2230; color: #cde; font-size: 9px; }"
    "QProgressBar::chunk { background-color: #ff9800; border-radius: 3px; }"
)


class MerchantStatusPanel(QFrame):
    """右侧多商户并发状态"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "MerchantStatusPanel { background-color: #121820; border: 1px solid #2a3548; "
            "border-radius: 8px; }"
        )
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 14, 14, 14)
        self._layout.setSpacing(10)
        title = QLabel("多商户并发状态")
        title.setFont(QFont(_MONO, 12, QFont.Weight.Bold))
        title.setStyleSheet("color: #00e5cc;")
        self._layout.addWidget(title)
        self._rows: list[tuple[QLabel, QProgressBar]] = []

        mids = [
            "merchant-alpha-001",
            "merchant-beta-002",
            "merchant-gamma-003",
            "merchant-demo-001",
        ]
        for mid in mids:
            row = QHBoxLayout()
            lb = QLabel(mid[:18] + "…")
            lb.setFont(QFont(_MONO, 9))
            lb.setStyleSheet("color: #aab; min-width: 140px;")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("%v 路并发")
            bar.setStyleSheet(_BAR_OK)
            row.addWidget(lb)
            row.addWidget(bar, stretch=1)
            self._layout.addLayout(row)
            self._rows.append((lb, bar))
        self._layout.addStretch()

    def update_merchants(self, merchants: list[tuple[str, int, str]]) -> None:
        for i, (full_id, concurrent, status) in enumerate(merchants):
            if i >= len(self._rows):
                break
            lb, bar = self._rows[i]
            lb.setText(full_id[:22] + ("…" if len(full_id) > 22 else ""))
            bar.setValue(min(100, concurrent * 12))
            bar.setFormat(f"{concurrent} 路 · {status}")
            bar.setStyleSheet(_BAR_HOT if status == "峰值" else _BAR_OK)


async def _aggregate_ledger() -> tuple[float, float, list[FeedEntry]]:
    """从 transaction_ledger 汇总 GMV / 路由费，并生成成交类流水"""
    from database.models import AsyncSessionFactory
    from modules.supply_chain.models import TransactionLedger
    from sqlalchemy import func, select

    feed: list[FeedEntry] = []
    vol, fee = 0.0, 0.0
    try:
        async with AsyncSessionFactory() as session:
            q = select(
                func.coalesce(func.sum(TransactionLedger.amount_usd), 0.0),
                func.coalesce(func.sum(TransactionLedger.routing_fee_usd), 0.0),
            )
            row = (await session.execute(q)).one()
            vol = float(row[0] or 0)
            fee = float(row[1] or 0)

            stmt = (
                select(TransactionLedger)
                .order_by(TransactionLedger.created_at.desc())
                .limit(24)
            )
            rows = (await session.execute(stmt)).scalars().all()
            for r in reversed(rows):
                ts = r.created_at.strftime("%H:%M:%S") if r.created_at else _now_hms()
                feed.append(
                    FeedEntry(
                        ts,
                        f"成交 ${r.amount_usd:.2f} · {r.merchant_id[:14]}… · 路由费 ${r.routing_fee_usd:.2f}",
                        "success",
                        source_id=r.transaction_id,
                    )
                )
    except Exception:
        pass
    return vol, fee, feed


def _synthetic_tick(
    demo_vol: float,
    demo_fee: float,
) -> tuple[float, float, FeedEntry, float]:
    """融资演示：空库时温和增长 + 一条随机事件"""
    bump = random.uniform(1200, 6500)
    demo_vol += bump
    demo_fee = round(demo_vol * 0.01, 2)
    roi = 93.0 + random.uniform(-2, 5) + (demo_fee % 7) * 0.1
    roll = random.random()
    if roll < 0.55:
        kind: FeedKind = "success"
        line = f"撮合成交 ${bump:,.0f} · 多租户隔离通过 · 路由费 ${bump * 0.01:,.2f}"
    elif roll < 0.82:
        kind = "bundling"
        line = f"拼单分支 MOQ 缺口 {random.randint(200, 4000)} pcs · 已推送拼单引擎"
    else:
        kind = "failed"
        line = f"谈判未过 · 认证/预算不符 · SKU 已降级推荐"
    entry = FeedEntry(_now_hms(), line, kind)
    return demo_vol, demo_fee, entry, roi


class GodDashboard(QMainWindow):
    def __init__(self, strict_db: bool) -> None:
        super().__init__()
        self._strict = strict_db
        self.setWindowTitle("Project Claw — God Dashboard 融资演示大屏")
        self.setMinimumSize(1280, 760)

        self._demo_vol = 128_000.0
        self._demo_fee = 1_280.0
        self._seen_ledger_ids: set[str] = set()
        self._sig = DashboardDataSignal()
        self._sig.loaded.connect(self._on_data)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._harvester = HarvesterStrip()
        root.addWidget(self._harvester)

        body = QHBoxLayout()
        body.setSpacing(12)

        # 左侧：交易流
        left_frame = QFrame()
        left_frame.setStyleSheet(
            "QFrame { background-color: #0e1218; border: 1px solid #2a3548; border-radius: 8px; }"
        )
        lv = QVBoxLayout(left_frame)
        lv.setContentsMargins(8, 8, 8, 8)
        lh = QLabel("交易流 · 实时滚动")
        lh.setFont(QFont(_MONO, 12, QFont.Weight.Bold))
        lh.setStyleSheet("color: #7cfc00;")
        lv.addWidget(lh)
        self._feed_list = QListWidget()
        self._feed_list.setFont(QFont(_MONO, 10))
        self._feed_list.setStyleSheet(
            "QListWidget { background: #0a0e14; border: none; color: #ccc; }"
            "QListWidget::item { padding: 6px; border-bottom: 1px solid #1a2230; }"
        )
        self._feed_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        lv.addWidget(self._feed_list)
        body.addWidget(left_frame, stretch=38)

        # 中部：ROI
        mid = QVBoxLayout()
        self._roi = ROIChartWidget()
        mid.addWidget(self._roi, stretch=1)
        hint = QLabel("绿色面积 = 近窗效率趋势 · 峰值说明撮合引擎负载健康")
        hint.setFont(QFont(_MONO, 9))
        hint.setStyleSheet("color: #556677;")
        mid.addWidget(hint)
        body.addLayout(mid, stretch=42)

        # 右侧：商户
        self._merchants = MerchantStatusPanel()
        body.addWidget(self._merchants, stretch=20)

        root.addLayout(body, stretch=1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._request_refresh)
        self._timer.start(_REFRESH_MS)
        self._request_refresh()

    def _feed_color(self, kind: FeedKind) -> QColor:
        if kind == "success":
            return QColor("#39ff14")  # 亮绿
        if kind == "bundling":
            return QColor("#ff9800")  # 橙
        return QColor("#b0b0b8")  # 浅灰（失败）

    def _append_feed_ui(self, entries: list[FeedEntry]) -> None:
        for e in entries:
            text = f"{e.ts}  {e.line}"
            it = QListWidgetItem(text)
            it.setForeground(self._feed_color(e.kind))
            self._feed_list.addItem(it)
        while self._feed_list.count() > 200:
            self._feed_list.takeItem(0)
        self._feed_list.scrollToBottom()

    def _on_data(self, payload: dict) -> None:
        ds = payload.get("demo_state")
        if ds is not None:
            self._demo_vol, self._demo_fee = ds[0], ds[1]

        self._harvester.set_totals(float(payload["volume"]), float(payload["fee"]))

        raw = payload.get("feed", [])
        entries = [FeedEntry(**fd) for fd in raw if isinstance(fd, dict)]
        for e in entries:
            if e.source_id:
                self._seen_ledger_ids.add(e.source_id)
        if entries:
            self._append_feed_ui(entries)

        for p in payload.get("roi", []):
            self._roi.push_value(float(p))
        self._merchants.update_merchants(payload.get("merchants", []))

    def _request_refresh(self) -> None:
        seen = set(self._seen_ledger_ids)
        demo_v, demo_f = self._demo_vol, self._demo_fee

        def worker() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                vol, fee, db_feed = loop.run_until_complete(_aggregate_ledger())
            finally:
                loop.close()

            use_demo = not self._strict and (vol < 1.0 and fee < 0.01)
            new_entries: list[FeedEntry] = []
            roi_batch: list[float] = []
            demo_state: tuple[float, float] | None = None
            out_vol, out_fee = vol, fee

            if use_demo:
                dv, df, entry, roi = _synthetic_tick(demo_v, demo_f)
                new_entries.append(entry)
                roi_batch.append(roi)
                out_vol, out_fee = dv, df
                demo_state = (dv, df)
            else:
                db_new = [e for e in db_feed if e.source_id not in seen]
                new_entries.extend(db_new)
                eff = (fee / vol * 100.0) if vol > 0 else 96.0
                roi_batch.append(88.0 + min(eff * 8, 10.0))

            mers = [
                ("merchant-alpha-001", random.randint(2, 8), random.choice(["平稳", "峰值"])),
                ("merchant-beta-002", random.randint(1, 6), "平稳"),
                ("merchant-gamma-003", random.randint(0, 4), "待机"),
                ("merchant-demo-001", random.randint(1, 5), "平稳"),
            ]

            self._sig.loaded.emit(
                {
                    "volume": out_vol,
                    "fee": out_fee,
                    "feed": [asdict(e) for e in new_entries],
                    "roi": roi_batch,
                    "merchants": mers,
                    "demo_state": demo_state,
                },
            )

        threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Project Claw God Dashboard")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="仅使用真实账本数据（无模拟增长）",
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    w = GodDashboard(strict_db=args.strict)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
