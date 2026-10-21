from __future__ import annotations

import time

import os

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QLabel, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QFileDialog, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..monitor import TargetMonitor
from ..settings import Settings
from . import theme
from ..export import build_report
from .. import incidents
from ..history import History
from ..linkstats import LinkMonitor
from .diag_dialog import DiagnosticsDialog
from .history_dialog import HistoryDialog
from .target_tab import TargetTab


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings"); self.resize(600, 700)
        f = QFormLayout(self)
        self.interval = QDoubleSpinBox(); self.interval.setRange(0.2, 30); self.interval.setSingleStep(0.5); self.interval.setValue(s.interval); self.interval.setSuffix(" s")
        self.auto = QCheckBox("Run diagnostics automatically when a target stops responding"); self.auto.setChecked(s.auto_diag)
        self.outage = QSpinBox(); self.outage.setRange(3, 600); self.outage.setValue(s.outage_seconds); self.outage.setSuffix(" seconds without reply")
        self.cool = QSpinBox(); self.cool.setRange(1, 240); self.cool.setValue(s.cooldown_minutes); self.cool.setSuffix(" minutes between auto runs")
        self.echo = QLineEdit(s.echo_server); self.echo.setPlaceholderText("optional – host:40404 (run tools/echo_server.py there)")
        self.sites = QPlainTextEdit("\n".join(s.websites)); self.sites.setFixedHeight(110)
        self.sched = QSpinBox(); self.sched.setRange(0, 1440); self.sched.setValue(s.sched_minutes); self.sched.setSpecialValueText("off"); self.sched.setSuffix(" min")
        self.heavy = QCheckBox("Include speed + video tests in scheduled runs (uses data)"); self.heavy.setChecked(s.sched_heavy)
        self.upnp = QCheckBox("Read router traffic counters / port forwards via UPnP"); self.upnp.setChecked(s.upnp_enabled)
        self.snmp_host = QLineEdit(s.snmp_host); self.snmp_host.setPlaceholderText("optional – router IP for SNMP, e.g. 192.168.1.1")
        self.snmp_comm = QLineEdit(s.snmp_community)
        self.snmp_if = QSpinBox(); self.snmp_if.setRange(0, 9999); self.snmp_if.setValue(s.snmp_ifindex); self.snmp_if.setSpecialValueText("unset")
        self.cap = QDoubleSpinBox(); self.cap.setRange(0, 100000); self.cap.setValue(s.link_up_mbps); self.cap.setSpecialValueText("auto"); self.cap.setSuffix(" Mbps")
        self.plexscan = QCheckBox("Scan my local network for Plex servers and check their version"); self.plexscan.setChecked(s.plex_scan)
        self.video = QLineEdit(s.video_url); self.video.setPlaceholderText("blank = built-in sample videos")
        f.addRow("Probe interval", self.interval); f.addRow(self.auto); f.addRow("Auto-trigger after", self.outage)
        f.addRow("Cooldown", self.cool); f.addRow("Echo server", self.echo); f.addRow("Websites to test (one per line)", self.sites)
        f.addRow("Video test URL", self.video)
        f.addRow(self.upnp); f.addRow("Router SNMP host", self.snmp_host); f.addRow("SNMP community", self.snmp_comm)
        f.addRow("SNMP WAN ifIndex", self.snmp_if); f.addRow("My upload capacity", self.cap); f.addRow(self.plexscan)
        f.addRow("Run tests every (builds the test history)", self.sched); f.addRow(self.heavy)
        b = QPushButton("Save"); b.setObjectName("primary"); b.clicked.connect(self.accept); f.addRow(b)

    def apply(self, s: Settings):
        s.interval, s.auto_diag = self.interval.value(), self.auto.isChecked()
        s.outage_seconds, s.cooldown_minutes = self.outage.value(), self.cool.value()
        s.upnp_enabled, s.snmp_host, s.snmp_community = self.upnp.isChecked(), self.snmp_host.text().strip(), self.snmp_comm.text().strip()
        s.snmp_ifindex, s.link_up_mbps, s.plex_scan = self.snmp_if.value(), self.cap.value(), self.plexscan.isChecked()
        s.sched_minutes, s.sched_heavy = self.sched.value(), self.heavy.isChecked()
        s.echo_server, s.video_url = self.echo.text().strip(), self.video.text().strip()
        s.websites = [x.strip() for x in self.sites.toPlainText().splitlines() if x.strip()]


class OutageBridge(QObject):
    outage = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, history: History | None = None):
        super().__init__()
        self.s = settings
        self.hist = history
        self.setWindowTitle("PingRate – network ping & diagnostics")
        self.resize(1250, 880)
        self.last_auto = 0.0
        self.bridge = OutageBridge(); self.bridge.outage.connect(self._on_outage)
        self.link = LinkMonitor(self.s, self.hist)
        self.alerts: list = []
        self.alert_seen: dict[str, float] = {}
        self.diag = DiagnosticsDialog(self.s, self._collect, self.hist, self, extra=self._extra)
        self.history_dlg = HistoryDialog(self.hist, lambda: [self.tabs.widget(i).mon.target for i in range(self.tabs.count())], self) if self.hist else None

        root = QWidget(); root.setObjectName("root"); self.setCentralWidget(root)
        lay = QVBoxLayout(root); lay.setContentsMargins(16, 14, 16, 8)
        top = QHBoxLayout()
        self.entry = QLineEdit(); self.entry.setPlaceholderText("Add a domain or IP to monitor – e.g. google.com or 8.8.8.8")
        self.entry.returnPressed.connect(self._add_from_entry)
        b_add = QPushButton("Add target"); b_add.setObjectName("primary"); b_add.clicked.connect(self._add_from_entry)
        b_diag = QPushButton("🩺  Diagnostics"); b_diag.setObjectName("diag"); b_diag.clicked.connect(lambda: self._open_diag(manual=True))
        b_hist = QPushButton("📈  History"); b_hist.clicked.connect(self._open_history); b_hist.setEnabled(bool(self.hist))
        b_full = QPushButton("📄  Full output"); b_full.setToolTip("Save everything to one file you can give to an AI / support"); b_full.clicked.connect(self._full_output)
        b_set = QPushButton("Settings"); b_set.clicked.connect(self._settings)
        top.addWidget(self.entry, 1); top.addWidget(b_add); top.addSpacing(12); top.addWidget(b_diag); top.addWidget(b_hist)
        top.addWidget(b_full); top.addWidget(b_set)
        lay.addLayout(top)
        self.alert_bar = QLabel(); self.alert_bar.setWordWrap(True); self.alert_bar.setVisible(False)
        self.alert_bar.setTextFormat(Qt.TextFormat.RichText); self.alert_bar.setOpenExternalLinks(False)
        lay.addWidget(self.alert_bar)
        self.tabs = QTabWidget(); self.tabs.setTabsClosable(True); self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        lay.addWidget(self.tabs, 1)
        self.sb = self.statusBar()
        for t in self.s.targets:
            self._add(t)
        self.last_sched = time.time()
        self.watch = QTimer(self); self.watch.timeout.connect(self._watch); self.watch.start(20000)
        self.tick = QTimer(self); self.tick.timeout.connect(self._tick); self.tick.start(1000)
        self._tick()

    # ------------------------------------------------------------------
    def _add_from_entry(self):
        t = self.entry.text().strip().replace("http://", "").replace("https://", "").split("/")[0]
        if t:
            self._add(t); self.entry.clear()
            if t not in self.s.targets:
                self.s.targets.append(t); self.s.save()

    def _add(self, target):
        for i in range(self.tabs.count()):
            if self.tabs.widget(i).mon.target.lower() == target.lower():
                self.tabs.setCurrentIndex(i); return
        mon = TargetMonitor(target, self.s.interval, self.s.outage_seconds, on_outage=self.bridge.outage.emit, history=self.hist)
        tab = TargetTab(mon)
        mon.start()
        self.tabs.setCurrentIndex(self.tabs.addTab(tab, target))

    def _close_tab(self, i):
        w = self.tabs.widget(i)
        w.close_tab()
        t = w.mon.target
        if t in self.s.targets:
            self.s.targets.remove(t); self.s.save()
        self.tabs.removeTab(i)

    def _settings(self):
        d = SettingsDialog(self.s, self)
        if d.exec():
            d.apply(self.s); self.s.save()
            for i in range(self.tabs.count()):
                m = self.tabs.widget(i).mon
                m.interval, m.outage_seconds = self.s.interval, self.s.outage_seconds

    def _open_history(self):
        self.history_dlg.show(); self.history_dlg.raise_(); self.history_dlg.activateWindow()

    def _full_output(self):
        ask = QMessageBox.question(self, "Full output",
                                   "Run fresh diagnostics first? (about a minute – makes the file much more useful)\n\n"
                                   "Yes = run tests, then save   ·   No = save what exists now",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
        if ask == QMessageBox.StandardButton.Cancel:
            return
        if ask == QMessageBox.StandardButton.Yes and not self.diag.running:
            self.diag.completed.connect(self._save_full, Qt.ConnectionType.SingleShotConnection)
            self._open_diag(manual=True, reason="Collecting data for the full output file…")
        else:
            self._save_full()

    def _save_full(self):
        name = time.strftime("pingrate-full-%Y%m%d-%H%M%S.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Save full output", os.path.join(os.path.expanduser("~"), "Documents", name)
                                              if os.path.isdir(os.path.join(os.path.expanduser("~"), "Documents")) else name, "Text (*.txt)")
        if not path:
            return
        self.sb.showMessage("Collecting data…")
        QApplication.processEvents()
        mons = [self.tabs.widget(i).mon for i in range(self.tabs.count())]
        text = build_report(self.s, mons, self.diag.results or None, self.diag.last_verdict, self.hist, link=self.link)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self.sb.showMessage(f"Saved {path}", 8000)
            QMessageBox.information(self, "Saved", f"Everything was saved to:\n{path}\n\nGive this file to an AI assistant (or support) – "
                                                   "it starts with instructions explaining what to do with it.")
        except OSError as e:
            QMessageBox.warning(self, "Could not save", str(e))

    def _extra(self):
        now = time.time()
        drops = []
        for i in range(self.tabs.count()):
            m = self.tabs.widget(i).mon
            drops.append((m.target, m.events(now - 7 * 86400), m.ongoing_drop_since))
        return {"link": self.link, "drops": drops}

    def _watch(self):
        """Evaluate drop-pattern and link-load alerts; show a calm banner; auto-run diagnostics on a new serious pattern."""
        now = time.time()
        alerts = []
        for target, ev, ongoing in self._extra()["drops"]:
            alerts += incidents.analyze_drops(ev, now, ongoing, target)
        alerts += incidents.analyze_link(list(self.link.samples), self.link.cap_up, now)
        self.alerts = alerts
        if not alerts:
            self.alert_bar.setVisible(False)
            return
        worst = "bad" if any(a.level == "bad" for a in alerts) else "warn"
        col = theme.LEVEL[worst]
        body = "<br>".join(f"• {a.title}" for a in alerts[:4])
        self.alert_bar.setText(f"<b>Worth a look</b><br>{body}<br><span style='color:{theme.MUTED}'>Often something ordinary (a big upload, "
                               "router restart, Wi‑Fi). Run 🩺 Diagnostics for a ranked list of likely causes, or open 📈 History → "
                               "Drops &amp; link for details.</span>")
        self.alert_bar.setStyleSheet(f"background:{theme.PANEL};border:1px solid {col};border-left:5px solid {col};border-radius:8px;padding:10px;")
        self.alert_bar.setVisible(True)
        fresh = [a for a in alerts if a.level == "bad" and now - self.alert_seen.get(a.key, 0) > 1800]
        for a in alerts:
            self.alert_seen.setdefault(a.key, 0)
        if fresh:
            for a in fresh:
                self.alert_seen[a.key] = now
            if (self.s.auto_diag and not self.diag.running and now - self.last_auto >= self.s.cooldown_minutes * 60):
                self.last_auto = now
                self._open_diag(reason=f"⚡ Started automatically: {fresh[0].title}")

    def _collect(self):
        out = []
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i); f = w.mon.finding()
            out.append((w.mon.target, f.level, f.title, f.detail))
        return out

    def _open_diag(self, manual=False, reason=""):
        self.diag.show(); self.diag.raise_(); self.diag.activateWindow()
        if manual or reason:
            self.diag.start(reason)

    def _on_outage(self, mon):
        cool = self.s.cooldown_minutes * 60
        if not self.s.auto_diag or self.diag.running or time.time() - self.last_auto < cool:
            return
        self.last_auto = time.time()
        self._open_diag(reason=f"⚡ Started automatically: {mon.target} stopped responding for {self.s.outage_seconds} seconds.")

    def _tick(self):
        if (self.s.sched_minutes and not self.diag.running and time.time() - self.last_sched >= self.s.sched_minutes * 60):
            self.last_sched = time.time()
            self.diag.start("Scheduled background run", scheduled=True)        # runs silently; results go to History
        n_bad = 0
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            self.tabs.tabBar().setTabTextColor(i, QColor(theme.LEVEL.get(w.level, theme.MUTED)))
            n_bad += w.level == "bad"
        auto = f"auto-diagnostics: after {self.s.outage_seconds}s of no reply" if self.s.auto_diag else "auto-diagnostics: off"
        self.sb.showMessage(f"{self.tabs.count()} target(s) · {n_bad} with problems · {auto}")

    def closeEvent(self, e):
        for i in range(self.tabs.count()):
            self.tabs.widget(i).close_tab()
        self.link.stop()
        if self.hist:
            self.hist.flush()
        super().closeEvent(e)
