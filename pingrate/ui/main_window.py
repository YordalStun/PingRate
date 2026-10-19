from __future__ import annotations

import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..monitor import TargetMonitor
from ..settings import Settings
from . import theme
from .diag_dialog import DiagnosticsDialog
from .target_tab import TargetTab


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings"); self.resize(520, 440)
        f = QFormLayout(self)
        self.interval = QDoubleSpinBox(); self.interval.setRange(0.2, 30); self.interval.setSingleStep(0.5); self.interval.setValue(s.interval); self.interval.setSuffix(" s")
        self.auto = QCheckBox("Run diagnostics automatically when a target stops responding"); self.auto.setChecked(s.auto_diag)
        self.outage = QSpinBox(); self.outage.setRange(3, 600); self.outage.setValue(s.outage_seconds); self.outage.setSuffix(" seconds without reply")
        self.cool = QSpinBox(); self.cool.setRange(1, 240); self.cool.setValue(s.cooldown_minutes); self.cool.setSuffix(" minutes between auto runs")
        self.echo = QLineEdit(s.echo_server); self.echo.setPlaceholderText("optional – host:40404 (run tools/echo_server.py there)")
        self.sites = QPlainTextEdit("\n".join(s.websites)); self.sites.setFixedHeight(110)
        self.video = QLineEdit(s.video_url); self.video.setPlaceholderText("blank = built-in sample videos")
        f.addRow("Probe interval", self.interval); f.addRow(self.auto); f.addRow("Auto-trigger after", self.outage)
        f.addRow("Cooldown", self.cool); f.addRow("Echo server", self.echo); f.addRow("Websites to test (one per line)", self.sites)
        f.addRow("Video test URL", self.video)
        b = QPushButton("Save"); b.setObjectName("primary"); b.clicked.connect(self.accept); f.addRow(b)

    def apply(self, s: Settings):
        s.interval, s.auto_diag = self.interval.value(), self.auto.isChecked()
        s.outage_seconds, s.cooldown_minutes = self.outage.value(), self.cool.value()
        s.echo_server, s.video_url = self.echo.text().strip(), self.video.text().strip()
        s.websites = [x.strip() for x in self.sites.toPlainText().splitlines() if x.strip()]


class OutageBridge(QObject):
    outage = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self.setWindowTitle("PingRate – network ping & diagnostics")
        self.resize(1250, 880)
        self.last_auto = 0.0
        self.bridge = OutageBridge(); self.bridge.outage.connect(self._on_outage)
        self.diag = DiagnosticsDialog(self.s, self._collect, self)

        root = QWidget(); root.setObjectName("root"); self.setCentralWidget(root)
        lay = QVBoxLayout(root); lay.setContentsMargins(16, 14, 16, 8)
        top = QHBoxLayout()
        self.entry = QLineEdit(); self.entry.setPlaceholderText("Add a domain or IP to monitor – e.g. google.com or 8.8.8.8")
        self.entry.returnPressed.connect(self._add_from_entry)
        b_add = QPushButton("Add target"); b_add.setObjectName("primary"); b_add.clicked.connect(self._add_from_entry)
        b_diag = QPushButton("🩺  Diagnostics"); b_diag.setObjectName("diag"); b_diag.clicked.connect(lambda: self._open_diag(manual=True))
        b_set = QPushButton("Settings"); b_set.clicked.connect(self._settings)
        top.addWidget(self.entry, 1); top.addWidget(b_add); top.addSpacing(12); top.addWidget(b_diag); top.addWidget(b_set)
        lay.addLayout(top)
        self.tabs = QTabWidget(); self.tabs.setTabsClosable(True); self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        lay.addWidget(self.tabs, 1)
        self.sb = self.statusBar()
        for t in self.s.targets:
            self._add(t)
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
        mon = TargetMonitor(target, self.s.interval, self.s.outage_seconds, on_outage=self.bridge.outage.emit)
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
        super().closeEvent(e)
