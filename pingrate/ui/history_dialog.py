from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import theme

RANGES = {"Last 24 hours": 1, "Last 3 days": 3, "Last 7 days": 7}
_f = lambda v: "–" if v is None else f"{v:.0f}" if v >= 10 else f"{v:.1f}"
_t = lambda t: time.strftime("%a %d %b %H:%M", time.localtime(t))


def _table(cols):
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return t


class HistoryDialog(QDialog):
    def __init__(self, history, current_targets, parent=None):
        super().__init__(parent)
        self.h, self.current_targets = history, current_targets
        self.setWindowTitle("PingRate – History")
        self.resize(1050, 820)
        lay = QVBoxLayout(self)
        tabs = QTabWidget(); lay.addWidget(tabs)

        # ---- connection history
        w = QWidget(); v = QVBoxLayout(w)
        row = QHBoxLayout()
        self.target = QComboBox(); self.target.setMinimumWidth(240)
        self.range = QComboBox(); self.range.addItems(RANGES); self.range.setCurrentIndex(2)
        b = QPushButton("Refresh"); b.clicked.connect(self.refresh)
        row.addWidget(QLabel("Target:")); row.addWidget(self.target); row.addWidget(self.range); row.addWidget(b); row.addStretch()
        v.addLayout(row)
        self.summary = QLabel(""); self.summary.setWordWrap(True); v.addWidget(self.summary)
        pg.setConfigOptions(antialias=True, background=theme.PANEL, foreground=theme.MUTED)
        self.p_loss = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        self.p_lat = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
        for p, lbl in ((self.p_loss, "Packet loss %"), (self.p_lat, "Latency ms")):
            p.showGrid(x=True, y=True, alpha=0.15); p.setLabel("left", lbl); p.setMouseEnabled(True, False); p.hideButtons()
        self.p_lat.setXLink(self.p_loss)
        v.addWidget(self.p_loss, 2); v.addWidget(self.p_lat, 2)
        sp = QSplitter(Qt.Orientation.Horizontal)
        self.t_out = _table(["Outage start", "End", "Length", "Loss"])
        self.t_hop = _table(["Hop", "Address", "Loss", "Avg ms", "Probes"])
        sp.addWidget(self.t_out); sp.addWidget(self.t_hop)
        v.addWidget(sp, 2)
        tabs.addTab(w, "Connection history")

        # ---- diagnostics runs
        d = QWidget(); dv = QVBoxLayout(d)
        s2 = QSplitter(Qt.Orientation.Horizontal)
        self.runs = QTreeWidget(); self.runs.setHeaderLabels(["When", "Result"]); self.runs.setColumnWidth(0, 150)
        self.runs.currentItemChanged.connect(self._show_run)
        self.report = QPlainTextEdit(); self.report.setReadOnly(True)
        self.report.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 12px;")
        s2.addWidget(self.runs); s2.addWidget(self.report); s2.setSizes([380, 650])
        dv.addWidget(s2)
        bc = QPushButton("Copy selected report"); bc.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.report.toPlainText()))
        dv.addWidget(bc, 0, Qt.AlignmentFlag.AlignRight)
        tabs.addTab(d, "Diagnostics runs")
        self.target.currentIndexChanged.connect(self.refresh)
        self.range.currentIndexChanged.connect(self.refresh)

    def showEvent(self, e):
        super().showEvent(e)
        cur = self.target.currentText()
        names = list(dict.fromkeys(self.current_targets() + self.h.targets()))
        self.target.blockSignals(True)
        self.target.clear(); self.target.addItems(names)
        if cur in names:
            self.target.setCurrentText(cur)
        self.target.blockSignals(False)
        self.refresh()

    def refresh(self):
        t = self.target.currentText()
        days = RANGES[self.range.currentText()]
        since = time.time() - days * 86400
        bucket = {1: 300, 3: 900, 7: 1800}[days]
        self.p_loss.clear(); self.p_lat.clear()
        s = self.h.summary(t, since) if t else None
        if not s or not s["sent"]:
            self.summary.setText("No history stored for this target yet – it builds up while PingRate is running.")
            self.t_out.setRowCount(0); self.t_hop.setRowCount(0)
        else:
            cover = s["minutes"] / (days * 1440) * 100
            self.summary.setText(
                f"<b>{s['sent']:,}</b> probes over <b>{s['minutes']:,}</b> monitored minutes (≈{cover:.0f}% of the period). "
                f"Loss <b style='color:{theme.BAD if s['loss'] > 2 else theme.GOOD}'>{s['loss']:.2f}%</b> · "
                f"avg <b>{_f(s['avg'])} ms</b> · best {_f(s['min'])} ms · worst {_f(s['max'])} ms")
            ser = self.h.series(t, since, bucket)
            xs = [r["t"] + bucket / 2 for r in ser]
            self.p_loss.addItem(pg.BarGraphItem(x=xs, height=[r["loss"] for r in ser], width=bucket * 0.9,
                                                brush=pg.mkBrush(255, 92, 112, 190), pen=None))
            self.p_loss.setYRange(0, max(5, max(r["loss"] for r in ser) * 1.1), padding=0)
            ok = [r for r in ser if r["avg"] is not None]
            self.p_lat.plot([r["t"] + bucket / 2 for r in ok], [r["max"] for r in ok], pen=pg.mkPen(theme.WARN, width=1))
            self.p_lat.plot([r["t"] + bucket / 2 for r in ok], [r["avg"] for r in ok], pen=pg.mkPen(theme.ACCENT, width=2))
            self.p_loss.setXRange(since, time.time(), padding=0)
            self._fill_out(self.h.outages(t, since)); self._fill_hops(self.h.hop_loss(t, since))
        self._fill_runs()

    def _fill_out(self, outs):
        self.t_out.setRowCount(len(outs))
        for i, o in enumerate(reversed(outs)):
            vals = [_t(o["start"]), _t(o["end"]), f"{(o['end'] - o['start']) / 60:.0f} min", f"{o['loss']:.0f}%"]
            for c, x in enumerate(vals):
                self.t_out.setItem(i, c, QTableWidgetItem(x))
        if not outs:
            self.t_out.setRowCount(1); self.t_out.setItem(0, 0, QTableWidgetItem("No outages in this period 🎉"))

    def _fill_hops(self, hops):
        self.t_hop.setRowCount(len(hops))
        for i, h in enumerate(hops):
            vals = ["dest" if h["ttl"] == 0 else str(h["ttl"]), h["addr"] or "*", f"{h['loss']:.1f}%", _f(h["avg"]), f"{h['sent']:,}"]
            for c, x in enumerate(vals):
                it = QTableWidgetItem(x)
                if c == 2 and h["loss"] > 5:
                    it.setForeground(QBrush(QColor(theme.BAD)))
                self.t_hop.setItem(i, c, it)

    def _fill_runs(self):
        self.runs.clear()
        for r in self.h.diag_runs():
            worst = "bad" if any(l == "bad" for l, _ in r["verdict"]) else "warn" if any(l == "warn" for l, _ in r["verdict"]) else "ok"
            it = QTreeWidgetItem([_t(r["ts"]), (r["verdict"][0][1] if r["verdict"] else "")[:90]])
            it.setForeground(1, QBrush(QColor(theme.LEVEL[worst])))
            it.setData(0, Qt.ItemDataRole.UserRole, r["report"])
            it.setToolTip(1, r["reason"] or "manual run")
            self.runs.addTopLevelItem(it)
        if self.runs.topLevelItemCount():
            self.runs.setCurrentItem(self.runs.topLevelItem(0))
        else:
            self.report.setPlainText("No diagnostics runs saved yet.")

    def _show_run(self, cur, _prev):
        if cur:
            self.report.setPlainText(cur.data(0, Qt.ItemDataRole.UserRole) or "")
