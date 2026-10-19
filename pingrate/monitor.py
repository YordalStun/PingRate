"""Continuous monitoring of one target: destination ping plus per-hop route probing."""
from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import icmp
from .analysis import Finding, analyze_route
from .stats import HopStats

MAX_HOPS = 30


class TargetMonitor:
    def __init__(self, target: str, interval: float = 1.0, outage_seconds: int = 10, on_outage=None):
        self.target = target
        self.interval = interval
        self.outage_seconds = outage_seconds
        self.on_outage = on_outage
        self.ip: str | None = None
        self.error: str | None = None
        self.hops: list[HopStats] = []
        self.dest = HopStats(0)
        self.dest_ttl: int | None = None
        self.paused = False
        self.fail_since: float | None = None
        self._outage_fired = False
        self._retrace = threading.Event()
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=MAX_HOPS + 2)
        self._resolving: set[str] = set()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"mon-{target}")

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def retrace(self) -> None:
        self._retrace.set()

    def reset(self) -> None:
        self.dest.reset()
        for h in self.hops:
            h.reset()

    # ------------------------------------------------------------------ analysis
    def finding(self) -> Finding:
        if self.error:
            return Finding("bad", "Cannot resolve this name", self.error)
        return analyze_route(list(self.hops), self.dest, self.dest_ttl)

    @property
    def outage_for(self) -> float:
        return 0.0 if self.fail_since is None else time.time() - self.fail_since

    # ------------------------------------------------------------------ internals
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.ip = icmp.resolve(self.target)
                self.error = None
                break
            except OSError as e:
                self.error = f"DNS lookup failed ({e.strerror or e}). Check the name or your DNS."
                if self._stop.wait(3):
                    return
        self._trace()
        last_trace = time.time()
        while not self._stop.is_set():
            t0 = time.time()
            if self._retrace.is_set() or (self.dest_ttl is None and t0 - last_trace > 30):
                self._retrace.clear()
                self._trace()
                last_trace = time.time()
            if not self.paused:
                self._cycle()
            self._stop.wait(max(0.05, self.interval - (time.time() - t0)))

    def _trace(self) -> None:
        if not self.ip:
            return
        futs = [self._pool.submit(icmp.ping, self.ip, t, 1000) for t in range(1, MAX_HOPS + 1)]
        replies = [f.result() for f in futs]
        dest_ttl = next((i + 1 for i, r in enumerate(replies) if r.kind == icmp.OK), None)
        if dest_ttl:
            n = dest_ttl - 1
        else:
            known = [i + 1 for i, r in enumerate(replies) if r.kind == icmp.TTL_EXPIRED]
            n = min((known[-1] if known else 0) + 1, MAX_HOPS)
        old = {h.ttl: h for h in self.hops}
        hops = []
        for t in range(1, n + 1):
            h = old.get(t) or HopStats(t)
            r = replies[t - 1]
            if r.addr and r.kind == icmp.TTL_EXPIRED and not h.addr:
                h.addr = r.addr
            hops.append(h)
        self.hops, self.dest_ttl = hops, dest_ttl
        self.dest.ttl = dest_ttl or n + 1
        self.dest.addr = self.ip
        self._lookup(self.dest)
        for h in hops:
            self._lookup(h)

    def _cycle(self) -> None:
        ip = self.ip
        hops = list(self.hops)
        f_dest = self._pool.submit(icmp.ping, ip, 128, 1500)
        f_hops = [self._pool.submit(icmp.ping, ip, h.ttl, 1000) for h in hops]
        now = time.time()
        for h, f in zip(hops, f_hops):
            r = f.result()
            if r.kind in (icmp.TTL_EXPIRED, icmp.OK):
                h.add(r.rtt, r.addr, now)
                self._lookup(h)
            else:
                h.add(None, None, now)
        r = f_dest.result()
        ok = r.kind == icmp.OK
        self.dest.add(r.rtt if ok else None, None, now)
        if ok:
            self.fail_since, self._outage_fired = None, False
        else:
            self.fail_since = self.fail_since or now
            if (not self._outage_fired and time.time() - self.fail_since >= self.outage_seconds
                    and self.on_outage):
                self._outage_fired = True
                try:
                    self.on_outage(self)
                except Exception:
                    pass

    def _lookup(self, h: HopStats) -> None:
        a = h.addr
        if not a or h.host or a in self._resolving:
            return
        self._resolving.add(a)

        def work():
            try:
                name = socket.gethostbyaddr(a)[0]
            except OSError:
                name = ""
            h.host = name if name and name != a else ""
        threading.Thread(target=work, daemon=True).start()
