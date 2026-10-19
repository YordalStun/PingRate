from __future__ import annotations

import threading
import time
from collections import deque

MAX_SAMPLES = 7200   # ~2h at 1 probe/second


class HopStats:
    """Rolling statistics for one hop (or the destination)."""

    def __init__(self, ttl: int):
        self.ttl = ttl
        self.addr: str | None = None
        self.host: str | None = None
        self.sent = 0
        self.recv = 0
        self.total = 0.0
        self.best: float | None = None
        self.worst: float | None = None
        self.last: float | None = None
        self.samples: deque[tuple[float, float | None]] = deque(maxlen=MAX_SAMPLES)
        self.lock = threading.Lock()

    def add(self, rtt: float | None, addr: str | None = None, when: float | None = None) -> None:
        with self.lock:
            self.sent += 1
            if addr:
                self.addr = addr
            if rtt is not None:
                self.recv += 1
                self.total += rtt
                self.last = rtt
                self.best = rtt if self.best is None else min(self.best, rtt)
                self.worst = rtt if self.worst is None else max(self.worst, rtt)
            self.samples.append((when or time.time(), rtt))

    def recent(self, n: int) -> list[float | None]:
        with self.lock:
            s = list(self.samples)[-n:]
        return [v for _, v in s]

    def snapshot(self, since: float | None = None) -> list[tuple[float, float | None]]:
        with self.lock:
            s = list(self.samples)
        return [x for x in s if since is None or x[0] >= since]

    @property
    def loss_pct(self) -> float:
        return 0.0 if not self.sent else 100.0 * (self.sent - self.recv) / self.sent

    @property
    def avg(self) -> float | None:
        return self.total / self.recv if self.recv else None

    @property
    def jitter(self) -> float | None:
        vals = [v for v in self.recent(200) if v is not None]
        if len(vals) < 2:
            return None
        return sum(abs(b - a) for a, b in zip(vals, vals[1:])) / (len(vals) - 1)

    def reset(self) -> None:
        with self.lock:
            self.sent = self.recv = 0
            self.total = 0.0
            self.best = self.worst = self.last = None
            self.samples.clear()
