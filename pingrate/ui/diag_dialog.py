from __future__ import annotations

import dataclasses
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from ..diag import tests
from . import theme

ICON = {tests.PASS: ("✔", theme.GOOD), tests.WARN: ("⚠", theme.WARN), tests.FAIL: ("✘", theme.BAD), tests.SKIP: ("–", theme.MUTED)}


class Bridge(QObject):
    result = Signal(object)
    progress = Signal(str)
    done = Signal()


class DiagnosticsDialog(QDialog):
    completed = Signal()

    def __init__(self, settings, collect_targets, history=None, parent=None):
        super().__init__(parent)
        self.settings, self.collect_targets, self.history = settings, collect_targets, history
        self.last_verdict: list = []
        self.scheduled = False
        self.setWindowTitle("PingRate – Diagnostics")
        self.resize(900, 680)
        self.bridge = Bridge()
        self.bridge.result.connect(self._add)
        self.bridge.progress.connect(lambda m: self.status.setText(m))
        self.bridge.done.connect(self._finished)
        self.results: dict[str, tests.Result] = {}
        self.ctx = None
        self.running = False

        lay = QVBoxLayout(self); lay.setSpacing(10)
        self.reason = QLabel(""); self.reason.setStyleSheet(f"color:{theme.WARN};"); self.reason.setWordWrap(True)
        lay.addWidget(self.reason)
        self.verdict = QFrame(); self.verdict.setObjectName("card")
        vl = QVBoxLayout(self.verdict)
        self.vtitle = QLabel("Diagnosis"); self.vtitle.setStyleSheet("font-size:16px;font-weight:700;")
        self.vtext = QLabel("Press Run to test your connection."); self.vtext.setWordWrap(True)
        vl.addWidget(self.vtitle); vl.addWidget(self.vtext)
        lay.addWidget(self.verdict)
        self.bar = QProgressBar(); self.bar.setRange(0, len(tests.TESTS)); self.bar.setTextVisible(False)
        self.status = QLabel(""); self.status.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(self.bar); lay.addWidget(self.status)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["", "Test", "Result"])
        self.tree.setColumnWidth(0, 40); self.tree.setColumnWidth(1, 260); self.tree.setWordWrap(True)
        self.tree.setAlternatingRowColors(True); self.tree.setRootIsDecorated(True)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.btn_run = QPushButton("Run diagnostics"); self.btn_run.setObjectName("primary"); self.btn_run.clicked.connect(self.start)
        self.btn_stop = QPushButton("Stop"); self.btn_stop.clicked.connect(self.stop); self.btn_stop.setEnabled(False)
        b_copy = QPushButton("Copy report"); b_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.report()))
        b_save = QPushButton("Save report…"); b_save.clicked.connect(self._save)
        row.addWidget(self.btn_run); row.addWidget(self.btn_stop); row.addStretch(); row.addWidget(b_copy); row.addWidget(b_save)
        lay.addLayout(row)

    # ------------------------------------------------------------------
    def start(self, reason: str = "", scheduled: bool = False):
        if self.running:
            return
        self.scheduled = scheduled
        self.reason.setText(reason if isinstance(reason, str) else "")
        self.reason.setVisible(bool(self.reason.text()))
        self.tree.clear(); self.results = {}; self.bar.setValue(0)
        self.vtitle.setText("Running tests…"); self.vtext.setText("Please wait – this takes about a minute.")
        self._color(theme.ACCENT)
        skip = {"speed", "video"} if scheduled and not self.settings.sched_heavy else set()
        self.ctx = tests.Context(self.settings, self.collect_targets(), progress=self.bridge.progress.emit, skip=skip)
        self.running = True
        self.btn_run.setEnabled(False); self.btn_stop.setEnabled(True)
        self.started = time.strftime("%Y-%m-%d %H:%M:%S")

        def work():
            tests.run_all(self.ctx, self.bridge.result.emit)
            self.bridge.done.emit()
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.ctx:
            self.ctx.cancel.set()

    def _add(self, r: tests.Result):
        self.results[r.key] = r
        self.bar.setValue(len(self.results))
        sym, col = ICON[r.status]
        it = QTreeWidgetItem(["", r.name, r.summary])
        it.setText(0, sym)
        it.setForeground(0, QBrush(QColor(col))); it.setForeground(2, QBrush(QColor(col if r.status != tests.PASS else theme.TEXT)))
        for d in r.details:
            QTreeWidgetItem(it, ["", "", d]).setForeground(2, QBrush(QColor(theme.MUTED)))
        self.tree.addTopLevelItem(it)
        if r.status in (tests.FAIL, tests.WARN):
            it.setExpanded(True)

    def _finished(self):
        self.running = False
        self.btn_run.setEnabled(True); self.btn_stop.setEnabled(False)
        self.status.setText("Finished." if not self.ctx.cancel.is_set() else "Stopped.")
        v = tests.verdict(self.results)
        self.last_verdict = v
        worst = "bad" if any(l == "bad" for l, _ in v) else "warn" if any(l == "warn" for l, _ in v) else "ok"
        self.vtitle.setText({"bad": "Problem found", "warn": "Possible issues", "ok": "All clear"}[worst])
        self.vtext.setText("\n\n".join(("• " + s) for _, s in v))
        self._color(theme.LEVEL[worst])
        if self.history and self.results and not self.ctx.cancel.is_set():
            self.history.save_diag(self.reason.text(), [dataclasses.asdict(r) for r in self.results.values()], v, self.report(),
                                   tests.metrics(self.results), tests.statuses(self.results), getattr(self, 'scheduled', False))
        self.completed.emit()

    def _color(self, col):
        self.verdict.setStyleSheet(f"QFrame#card{{border:1px solid {col};border-left:5px solid {col};}}")
        self.vtitle.setStyleSheet(f"font-size:16px;font-weight:700;color:{col};")

    def report(self) -> str:
        out = [f"PingRate diagnostics – {getattr(self, 'started', time.strftime('%Y-%m-%d %H:%M:%S'))}", ""]
        if self.reason.text():
            out += [self.reason.text(), ""]
        out += ["DIAGNOSIS", self.vtitle.text(), self.vtext.text(), "", "TESTS"]
        for r in self.results.values():
            out.append(f"[{r.status.upper():4}] {r.name}: {r.summary}")
            out += [f"         {d}" for d in r.details]
        return "\n".join(out)

    def _save(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save report", "pingrate-report.txt", "Text (*.txt)")
        if p:
            open(p, "w", encoding="utf-8").write(self.report())
