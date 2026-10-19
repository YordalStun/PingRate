from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..monitor import TargetMonitor
from . import theme

SPARK = Qt.ItemDataRole.UserRole + 1
WINDOWS = {"Last 1 min": 60, "Last 5 min": 300, "Last 30 min": 1800, "Last 2 h": 7200}


class SparkDelegate(QStyledItemDelegate):
    """Tiny bar chart of the last probes; lost probes are red ticks."""

    def paint(self, p: QPainter, opt, idx):
        super().paint(p, opt, idx)
        vals = idx.data(SPARK) or []
        if not vals:
            return
        r: QRect = opt.rect.adjusted(6, 5, -6, -5)
        ok = [v for v in vals if v is not None]
        top = max(max(ok) if ok else 1, 30.0)
        med = sorted(ok)[len(ok) // 2] if ok else 0
        slow = max(med * 1.6, med + 25)
        n = len(vals)
        w = r.width() / n
        p.save()
        for i, v in enumerate(vals):
            x = r.left() + i * w
            if v is None:
                p.fillRect(int(x), r.top(), max(1, int(w) - 0), r.height(), QColor(theme.BAD))
            else:
                h = max(2, int(r.height() * min(v / top, 1)))
                c = QColor(theme.GOOD if v <= slow else theme.WARN)
                p.fillRect(int(x), r.bottom() - h, max(1, int(w) - 0), h, c)
        p.restore()


def card(label: str):
    f = QFrame(); f.setObjectName("card")
    lay = QVBoxLayout(f); lay.setContentsMargins(14, 10, 14, 10); lay.setSpacing(2)
    a = QLabel(label); a.setObjectName("cardlabel")
    b = QLabel("–"); b.setObjectName("cardvalue")
    lay.addWidget(a); lay.addWidget(b)
    return f, b


class TargetTab(QWidget):
    COLS = ["Hop", "Address", "Host", "Status", "Loss", "Sent", "Last", "Avg", "Best", "Worst", "Jitter", "Recent"]

    def __init__(self, mon: TargetMonitor, parent=None):
        super().__init__(parent)
        self.mon = mon
        self.level = "wait"
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 12, 0, 0); lay.setSpacing(10)

        self.banner = QFrame(); self.banner.setObjectName("card")
        bl = QVBoxLayout(self.banner); bl.setContentsMargins(16, 12, 16, 12); bl.setSpacing(2)
        self.title = QLabel("Starting…"); self.title.setStyleSheet("font-size:17px;font-weight:700;")
        self.detail = QLabel(""); self.detail.setWordWrap(True); self.detail.setStyleSheet(f"color:{theme.MUTED};")
        bl.addWidget(self.title); bl.addWidget(self.detail)
        lay.addWidget(self.banner)

        row = QHBoxLayout(); row.setSpacing(10)
        self.cards = {}
        for k in ("Current", "Average", "Best / Worst", "Jitter", "Loss", "Sent / Received"):
            f, v = card(k); self.cards[k] = v; row.addWidget(f)
        lay.addLayout(row)

        ctl = QHBoxLayout()
        self.win = QComboBox(); self.win.addItems(WINDOWS)
        self.win.setCurrentIndex(1)
        self.btn_pause = QPushButton("Pause"); self.btn_pause.clicked.connect(self._pause)
        b_re = QPushButton("Re-trace route"); b_re.clicked.connect(mon.retrace)
        b_rs = QPushButton("Reset stats"); b_rs.clicked.connect(mon.reset)
        ctl.addWidget(QLabel("Graph:")); ctl.addWidget(self.win); ctl.addStretch()
        for b in (self.btn_pause, b_re, b_rs):
            ctl.addWidget(b)
        lay.addLayout(ctl)

        pg.setConfigOptions(antialias=True, background=theme.PANEL, foreground=theme.MUTED)
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(190)
        self.plot.showGrid(x=True, y=True, alpha=0.15)
        self.plot.setLabel("left", "Latency", units="ms")
        self.plot.setLabel("bottom", "seconds ago")
        self.plot.setMouseEnabled(False, False)
        self.plot.hideButtons()
        self.curve = self.plot.plot(pen=pg.mkPen(theme.ACCENT, width=2), fillLevel=0,
                                    brush=pg.mkBrush(76, 201, 240, 40))
        self.loss_bars = pg.BarGraphItem(x=[0], height=[0], width=0.8, brush=pg.mkBrush(255, 92, 112, 120), pen=None)
        self.plot.addItem(self.loss_bars)
        lay.addWidget(self.plot, 3)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setItemDelegateForColumn(len(self.COLS) - 1, SparkDelegate(self.table))
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setStretchLastSection(True)
        for i, w in enumerate((46, 120, 230, 190, 60, 54, 64, 64, 64, 64, 62, 200)):
            self.table.setColumnWidth(i, w)
        lay.addWidget(self.table, 4)

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(500)

    def _pause(self):
        self.mon.paused = not self.mon.paused
        self.btn_pause.setText("Resume" if self.mon.paused else "Pause")

    # ------------------------------------------------------------------ refresh
    def refresh(self):
        m = self.mon
        d = m.dest
        f = m.finding()
        self.level = f.level
        col = theme.LEVEL[f.level]
        self.banner.setStyleSheet(f"QFrame#card{{border:1px solid {col};border-left:5px solid {col};}}")
        ip = f"  ({m.ip})" if m.ip and m.ip != m.target else ""
        self.title.setText(f"{f.title}{ip}")
        self.title.setStyleSheet(f"font-size:17px;font-weight:700;color:{col};")
        self.detail.setText(f.detail)
        self.detail.setVisible(bool(f.detail))

        fmt = lambda v: "–" if v is None else f"{v:.0f} ms" if v >= 10 else f"{v:.1f} ms"
        c = self.cards
        c["Current"].setText(fmt(d.last) if d.recent(1) and d.recent(1)[0] is not None else "timeout")
        c["Average"].setText(fmt(d.avg)); c["Best / Worst"].setText(f"{fmt(d.best)} / {fmt(d.worst)}")
        c["Jitter"].setText(fmt(d.jitter))
        c["Loss"].setText(f"{d.loss_pct:.1f}%")
        c["Loss"].setStyleSheet(f"color:{theme.BAD if d.loss_pct > 5 else theme.GOOD if d.sent else theme.TEXT};")
        c["Sent / Received"].setText(f"{d.sent} / {d.recv}")
        self._graph(d)
        self._table(f)

    def _graph(self, d):
        span = WINDOWS[self.win.currentText()]
        now = time.time()
        s = d.snapshot(now - span)
        if not s:
            return
        ok = [(t - now, v) for t, v in s if v is not None]
        lost = [t - now for t, v in s if v is None]
        self.curve.setData([x for x, _ in ok], [y for _, y in ok], connect="finite")
        top = max([y for _, y in ok] + [20]) * 1.15
        bw = max(0.8, span / 400)
        self.loss_bars.setOpts(x=lost or [0], height=[top] * len(lost) if lost else [0], width=bw)
        have = now - s[0][0]
        self.plot.setXRange(-max(30.0, min(span, have * 1.05 + 2)), 0, padding=0)
        self.plot.setYRange(0, top, padding=0)

    def _table(self, f):
        m = self.mon
        rows = list(m.hops) + [m.dest]
        t = self.table
        if t.rowCount() != len(rows):
            t.setRowCount(len(rows))
        fmt = lambda v: "" if v is None else f"{v:.0f}" if v >= 10 else f"{v:.1f}"
        for i, h in enumerate(rows):
            is_dest = i == len(rows) - 1
            st = f.hop_status.get(i, ("wait", ""))
            code, label = st
            loss = h.loss_pct
            vals = [str(h.ttl if h.ttl else i + 1), h.addr or "*  no reply", (h.host or "") if not is_dest or h.host else (m.target if m.target != h.addr else ""),
                    ("▶ " if is_dest else "") + label + (" (destination)" if is_dest and label == "OK" else ""),
                    f"{loss:.0f}%", str(h.sent), fmt(h.last), fmt(h.avg), fmt(h.best), fmt(h.worst), fmt(h.jitter)]
            color = {"lost": theme.BAD, "down": theme.BAD, "slow": theme.WARN, "limited": theme.MUTED, "ok": theme.GOOD}.get(code, theme.MUTED)
            for c, v in enumerate(vals):
                it = t.item(i, c)
                if it is None:
                    it = QTableWidgetItem(); t.setItem(i, c, it)
                if it.text() != v:
                    it.setText(v)
                if c == 3:
                    it.setForeground(QBrush(QColor(color)))
                elif c == 4:
                    it.setForeground(QBrush(QColor(theme.BAD if loss > 5 else theme.TEXT)))
                else:
                    it.setForeground(QBrush(QColor(theme.TEXT if h.addr or c == 0 else theme.MUTED)))
                it.setBackground(QBrush(QColor(80, 20, 30) if code == "lost" else QColor(0, 0, 0, 0)))
            sp = t.item(i, len(self.COLS) - 1)
            if sp is None:
                sp = QTableWidgetItem(); t.setItem(i, len(self.COLS) - 1, sp)
            sp.setData(SPARK, h.recent(60))
            sp.setBackground(QBrush(QColor(80, 20, 30) if code == "lost" else QColor(0, 0, 0, 0)))
        t.viewport().update()

    def close_tab(self):
        self.timer.stop()
        self.mon.stop()
