"""Persistent history: per-minute aggregates (destination + every hop) and diagnostics runs."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

KEEP_DAYS = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS minute(
  target TEXT NOT NULL, ttl INTEGER NOT NULL, ts INTEGER NOT NULL, addr TEXT,
  sent INTEGER NOT NULL, recv INTEGER NOT NULL, sum REAL NOT NULL DEFAULT 0, min REAL, max REAL,
  PRIMARY KEY(target, ttl, ts));
CREATE TABLE IF NOT EXISTS diag(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, reason TEXT,
  verdict TEXT, results TEXT, report TEXT);
"""
_UPSERT = """
INSERT INTO minute(target,ttl,ts,addr,sent,recv,sum,min,max) VALUES(?,?,?,?,?,?,?,?,?)
ON CONFLICT(target,ttl,ts) DO UPDATE SET
  addr=COALESCE(excluded.addr, addr), sent=sent+excluded.sent, recv=recv+excluded.recv, sum=sum+excluded.sum,
  min=CASE WHEN min IS NULL THEN excluded.min WHEN excluded.min IS NULL THEN min ELSE MIN(min,excluded.min) END,
  max=CASE WHEN max IS NULL THEN excluded.max WHEN excluded.max IS NULL THEN max ELSE MAX(max,excluded.max) END
"""


class History:
    def __init__(self, path: str | Path | None = None, autoflush: bool = True):
        if path is None:
            from .settings import data_dir
            path = data_dir() / "history.db"
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.RLock()
        self.buf: dict[tuple, list] = {}
        with self.lock:
            self.db.executescript(_SCHEMA)
            self.prune()
        if autoflush:
            threading.Thread(target=self._loop, daemon=True, name="history-flush").start()

    # ------------------------------------------------------------------ writing
    def add(self, target: str, ttl: int, rtt: float | None, addr: str | None = None, ts: float | None = None):
        """ttl 0 = the destination itself."""
        minute = int((ts or time.time()) // 60 * 60)
        with self.lock:
            b = self.buf.setdefault((target, ttl, minute), [0, 0, 0.0, None, None, None])
            b[0] += 1
            if addr:
                b[5] = addr
            if rtt is not None:
                b[1] += 1; b[2] += rtt
                b[3] = rtt if b[3] is None else min(b[3], rtt)
                b[4] = rtt if b[4] is None else max(b[4], rtt)

    def flush(self):
        with self.lock:
            rows, self.buf = self.buf, {}
            try:
                self.db.executemany(_UPSERT, [(t, ttl, m, b[5], b[0], b[1], b[2], b[3], b[4])
                                              for (t, ttl, m), b in rows.items()])
                self.db.commit()
            except sqlite3.Error:
                pass

    def _loop(self):
        n = 0
        while True:
            time.sleep(20)
            self.flush()
            n += 1
            if n % 180 == 0:
                self.prune()

    def prune(self):
        cut = int(time.time() - KEEP_DAYS * 86400)
        try:
            self.db.execute("DELETE FROM minute WHERE ts<?", (cut,))
            self.db.execute("DELETE FROM diag WHERE ts<?", (cut - 22 * 86400,))   # reports kept 30 days
            self.db.commit()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------------ reading
    def targets(self) -> list[str]:
        self.flush()
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT DISTINCT target FROM minute ORDER BY target")]

    def series(self, target: str, since: float, bucket: int = 3600) -> list[dict]:
        self.flush()
        with self.lock:
            q = self.db.execute(
                "SELECT (ts/?)*? b, SUM(sent), SUM(recv), SUM(sum), MIN(min), MAX(max) FROM minute "
                "WHERE target=? AND ttl=0 AND ts>=? GROUP BY b ORDER BY b", (bucket, bucket, target, int(since))).fetchall()
        out = []
        for b, sent, recv, tot, mn, mx in q:
            out.append(dict(t=b, sent=sent, recv=recv, loss=100.0 * (sent - recv) / sent if sent else 0.0,
                            avg=tot / recv if recv else None, min=mn, max=mx))
        return out

    def summary(self, target: str, since: float) -> dict:
        self.flush()
        with self.lock:
            sent, recv, tot, mn, mx, n = self.db.execute(
                "SELECT SUM(sent),SUM(recv),SUM(sum),MIN(min),MAX(max),COUNT(*) FROM minute "
                "WHERE target=? AND ttl=0 AND ts>=?", (target, int(since))).fetchone()
        sent, recv = sent or 0, recv or 0
        return dict(sent=sent, recv=recv, loss=100.0 * (sent - recv) / sent if sent else 0.0,
                    avg=(tot / recv) if recv else None, min=mn, max=mx, minutes=n or 0)

    def hop_loss(self, target: str, since: float) -> list[dict]:
        self.flush()
        with self.lock:
            q = self.db.execute(
                "SELECT ttl, MAX(addr), SUM(sent), SUM(recv), SUM(sum) FROM minute WHERE target=? AND ts>=? "
                "GROUP BY ttl ORDER BY CASE WHEN ttl=0 THEN 999 ELSE ttl END", (target, int(since))).fetchall()
        return [dict(ttl=t, addr=a, sent=s, recv=r, loss=100.0 * (s - r) / s if s else 0.0,
                     avg=(tot / r) if r else None) for t, a, s, r, tot in q]

    def outages(self, target: str, since: float, min_loss: float = 50.0) -> list[dict]:
        """Runs of consecutive minutes where the destination lost >= min_loss% of probes."""
        self.flush()
        with self.lock:
            rows = self.db.execute("SELECT ts,sent,recv FROM minute WHERE target=? AND ttl=0 AND ts>=? ORDER BY ts",
                                   (target, int(since))).fetchall()
        out: list[dict] = []
        for ts, sent, recv in rows:
            loss = 100.0 * (sent - recv) / sent if sent else 0.0
            if loss < min_loss:
                continue
            if out and ts - out[-1]["end"] <= 120:
                o = out[-1]; o["end"] = ts + 60; o["sent"] += sent; o["recv"] += recv
            else:
                out.append(dict(start=ts, end=ts + 60, sent=sent, recv=recv))
        for o in out:
            o["loss"] = 100.0 * (o["sent"] - o["recv"]) / o["sent"] if o["sent"] else 0.0
        return out

    # ------------------------------------------------------------------ diagnostics runs
    def save_diag(self, reason: str, results: list[dict], verdict: list, report: str) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO diag(ts,reason,verdict,results,report) VALUES(?,?,?,?,?)",
                                  (int(time.time()), reason, json.dumps(verdict), json.dumps(results), report))
            self.db.commit()
            return cur.lastrowid

    def diag_runs(self, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT id,ts,reason,verdict,results,report FROM diag ORDER BY ts DESC LIMIT ?",
                                   (limit,)).fetchall()
        return [dict(id=i, ts=ts, reason=r or "", verdict=json.loads(v or "[]"), results=json.loads(res or "[]"),
                     report=rep or "") for i, ts, r, v, res, rep in rows]

    def close(self):
        self.flush()
        with self.lock:
            self.db.close()
