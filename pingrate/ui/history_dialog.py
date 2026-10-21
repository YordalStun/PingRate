from __future__ import annotations

import time

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import incidents
from ..diag.tests import METRIC_INFO
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


TREND_RANGES = {"Last 24 hours": 1, "Last 3 days": 3, "Last 7 days": 7, "Last 30 days": 30, "All time": 0}
GROUPS = [
    ("Speed (Mbps)", ["speed_down", "speed_up"]),
    ("Bufferbloat – extra ping while line is busy (ms)", ["bloat_down", "bloat_up"]),
    ("UDP / game-traffic loss (%)", ["udp_loss", "echo_up_loss", "echo_down_loss"]),
    ("Ping to router & internet (ms)", ["router_ms", "internet_ms"]),
    ("Loss to router & internet (%)", ["router_loss", "internet_loss"]),
    ("DNS & website load time (ms)", ["dns_ms", "web_ms"]),
    ("Websites loaded (%)  ·  UDP source ports OK (of 12)", ["web_ok_pct", "udp_ports_ok"]),
    ("Video test: start time & frozen time (s)", ["video_startup_s", "video_stall_s"]),
    ("Video test: stalls  ·  throughput (Mbps)", ["video_stalls", "video_mbps"]),
]
COLORS = [theme.ACCENT, theme.GOOD, theme.WARN, "#b18cff", theme.BAD]
STATUS_COL = {"pass": theme.GOOD, "warn": theme.WARN, "fail": theme.BAD}


class TrendsTab(QWidget):
    def __init__(self, history):
        super().__init__()
        self.h = history
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.range = QComboBox(); self.range.addItems(TREND_RANGES); self.range.setCurrentIndex(2)
        self.range.currentIndexChanged.connect(self.refresh)
        self.info = QLabel(""); self.info.setStyleSheet(f"color:{theme.MUTED};")
        b = QPushButton("Refresh"); b.clicked.connect(self.refresh)
        row.addWidget(QLabel("Range:")); row.addWidget(self.range); row.addWidget(b); row.addWidget(self.info, 1)
        v.addLayout(row)
        self.area = QScrollArea(); self.area.setWidgetResizable(True); self.area.setFrameShape(QScrollArea.Shape.NoFrame); self.area.setStyleSheet(f'QScrollArea{{background:{theme.BG};border:0;}}')
        v.addWidget(self.area, 1)

    def refresh(self):
        days = TREND_RANGES[self.range.currentText()]
        since = time.time() - days * 86400 if days else 0
        inner = QWidget(); inner.setObjectName('root'); lay = QVBoxLayout(inner); lay.setSpacing(10)
        pg.setConfigOptions(antialias=True, background=theme.PANEL, foreground=theme.MUTED)
        plots = []
        stat = self.h.status_series(since)
        nruns = len({t for t, _, _ in stat})
        self.info.setText(f"{nruns} test run(s) in this range" if nruns else
                          "No test runs yet – press Diagnostics, or set 'Run tests every…' in Settings to build history automatically.")
        if stat:
            keys = list(dict.fromkeys(k for _, k, _ in stat))
            pl = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
            pl.setMinimumHeight(70 + 34 * len(keys)); pl.setTitle("Pass / warn / fail per test", color=theme.MUTED, size="10pt")
            pl.showGrid(x=True, y=False, alpha=0.15); pl.setMouseEnabled(True, False); pl.hideButtons()
            pl.getAxis("left").setWidth(80)
            pl.getAxis("left").setTicks([[(i, k) for i, k in enumerate(keys)]])
            pl.setYRange(-0.7, len(keys) - 0.3, padding=0)
            idx = {k: i for i, k in enumerate(keys)}
            pl.addItem(pg.ScatterPlotItem(x=[t for t, _, _ in stat], y=[idx[k] for _, k, _ in stat], symbol="s", size=11,
                                          pen=None, brush=[pg.mkBrush(STATUS_COL.get(st, theme.MUTED)) for _, _, st in stat]))
            lay.addWidget(pl); plots.append(pl)
        for title, keys in GROUPS:
            data = [(k, self.h.metric_series(k, since)) for k in keys]
            data = [(k, ser) for k, ser in data if ser]
            if not data:
                continue
            pl = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()})
            pl.setMinimumHeight(190); pl.setTitle(title, color=theme.TEXT, size="11pt")
            pl.showGrid(x=True, y=True, alpha=0.15); pl.setMouseEnabled(True, False); pl.hideButtons()
            pl.addLegend(offset=(-10, 10))
            for n, (k, ser) in enumerate(data):
                c = COLORS[n % len(COLORS)]
                lab, unit = METRIC_INFO.get(k, (k, ""))
                pl.plot([t for t, _ in ser], [v for _, v in ser], pen=pg.mkPen(c, width=2), symbol="o", symbolSize=6,
                        symbolBrush=c, symbolPen=None, name=f"{lab} ({unit})" if unit else lab)
            pl.getViewBox().setLimits(yMin=0)
            lay.addWidget(pl); plots.append(pl)
        for p in plots[1:]:
            p.setXLink(plots[0])
        lay.addStretch()
        self.area.setWidget(inner)


class DropsTab(QWidget):
    """Time-of-day histogram of drops, upload/download usage, and the list of drop events."""

    def __init__(self, history, targets_fn):
        super().__init__()
        self.h, self.targets_fn = history, targets_fn
        v = QVBoxLayout(self)
        row = QHBoxLayout()
        self.target = QComboBox(); self.target.setMinimumWidth(220)
        self.range = QComboBox(); self.range.addItems(["Last 24 hours", "Last 3 days", "Last 7 days"]); self.range.setCurrentIndex(2)
        b = QPushButton("Refresh"); b.clicked.connect(self.refresh)
        self.note = QLabel(""); self.note.setWordWrap(True); self.note.setStyleSheet(f"color:{theme.MUTED};")
        row.addWidget(QLabel("Target:")); row.addWidget(self.target); row.addWidget(self.range); row.addWidget(b); row.addStretch()
        v.addLayout(row); v.addWidget(self.note)
        pg.setConfigOptions(antialias=True, background=theme.PANEL, foreground=theme.MUTED)
        self.p_hour = pg.PlotWidget(); self.p_hour.setTitle("Drops by hour of day (same-time clustering = recurring background cause)", color=theme.MUTED, size="10pt")
        self.p_hour.getAxis("bottom").setTicks([[(i, f"{i:02d}") for i in range(0, 24, 2)]])
        self.p_link = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem()}); self.p_link.setTitle("Upload / download usage (Mbps)", color=theme.MUTED, size="10pt")
        for p in (self.p_hour, self.p_link):
            p.showGrid(x=True, y=True, alpha=0.15); p.setMouseEnabled(True, False); p.hideButtons()
        v.addWidget(self.p_hour, 2); v.addWidget(self.p_link, 3)
        self.table = _table(["Drop started", "Ended", "Length"]); v.addWidget(self.table, 2)
        self.target.currentIndexChanged.connect(self.refresh); self.range.currentIndexChanged.connect(self.refresh)

    def load_targets(self):
        cur = self.target.currentText()
        names = list(dict.fromkeys(self.targets_fn() + self.h.targets()))
        self.target.blockSignals(True); self.target.clear(); self.target.addItems(names)
        if cur in names:
            self.target.setCurrentText(cur)
        self.target.blockSignals(False)

    def refresh(self):
        t = self.target.currentText()
        days = {0: 1, 1: 3, 2: 7}[self.range.currentIndex()]
        since = time.time() - days * 86400
        ev = self.h.drops(t, since) if t else []
        self.p_hour.clear(); self.p_link.clear()
        hh = incidents.hour_histogram(ev, since)
        self.p_hour.addItem(pg.BarGraphItem(x=list(range(24)), height=hh, width=0.8, brush=pg.mkBrush(255, 180, 84, 200), pen=None))
        alerts = incidents.analyze_drops(ev, None, None, t) if t else []
        self.note.setText(f"{len(ev)} drop(s) (3+ failed pings in a row) in this period. " + " ".join(a.title for a in alerts))
        src = "router" if "router" in self.h.link_sources() else "pc"
        ser = self.h.link_series(src, since, {1: 300, 3: 900, 7: 1800}[days])
        if ser:
            xs = [r["t"] for r in ser]
            self.p_link.addLegend(offset=(-10, 10))
            self.p_link.plot(xs, [r["up"] / 1e6 for r in ser], pen=pg.mkPen(theme.WARN, width=2), name=f"Upload ({'router WAN' if src == 'router' else 'this PC'})")
            self.p_link.plot(xs, [r["down"] / 1e6 for r in ser], pen=pg.mkPen(theme.ACCENT, width=2), name="Download")
            self.p_link.getViewBox().setLimits(yMin=0)
        self.table.setRowCount(len(ev))
        for i, (a, b) in enumerate(reversed(ev)):
            for c, x in enumerate([_t(a), _t(b), f"{b - a:.0f} s"]):
                self.table.setItem(i, c, QTableWidgetItem(x))


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

        self.trends = TrendsTab(self.h)
        tabs.addTab(self.trends, "Test trends")
        self.drops_tab = DropsTab(self.h, self.current_targets)
        tabs.addTab(self.drops_tab, "Drops && link")

        # ---- diagnostics runs
        d = QWidget(); dv = QVBoxLayout(d)
        s2 = QSplitter(Qt.Orientation.Horizontal)
        self.runs = QTreeWidget(); self.runs.setHeaderLabels(["When", "Result"]); self.runs.setColumnWidth(0, 190)
        self.runs.currentItemChanged.connect(self._show_run)
        self.report = QPlainTextEdit(); self.report.setReadOnly(True)
        self.report.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 12px;")
        s2.addWidget(self.runs); s2.addWidget(self.report); s2.setSizes([380, 650])
        dv.addWidget(s2)
        bc = QPushButton("Copy selected report"); bc.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.report.toPlainText()))
        dv.addWidget(bc, 0, Qt.AlignmentFlag.AlignRight)
        tabs.addTab(d, "All test runs")
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
        self.drops_tab.load_targets()
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
        self.trends.refresh()
        self.drops_tab.refresh()

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
        for r in self.h.diag_runs(with_report=False):
            worst = "bad" if any(l == "bad" for l, _ in r["verdict"]) else "warn" if any(l == "warn" for l, _ in r["verdict"]) else "ok"
            it = QTreeWidgetItem([_t(r["ts"]) + ("  ⏱" if r["scheduled"] else ""), (r["verdict"][0][1] if r["verdict"] else "")[:90]])
            it.setForeground(1, QBrush(QColor(theme.LEVEL[worst])))
            it.setData(0, Qt.ItemDataRole.UserRole, r["id"])
            it.setToolTip(1, r["reason"] or "manual run"); it.setToolTip(0, "⏱ = scheduled background run")
            self.runs.addTopLevelItem(it)
        if self.runs.topLevelItemCount():
            self.runs.setCurrentItem(self.runs.topLevelItem(0))
        else:
            self.report.setPlainText("No diagnostics runs saved yet.")

    def _show_run(self, cur, _prev):
        if cur:
            self.report.setPlainText(self.h.get_report(cur.data(0, Qt.ItemDataRole.UserRole)))
