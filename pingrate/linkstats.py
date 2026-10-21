"""Background sampler: this PC's NIC counters + (optionally) the router's WAN counters via UPnP or SNMP."""
from __future__ import annotations

import threading
import time
from collections import deque

from . import snmp, upnp
from .incidents import LinkSample

INTERVAL = 5.0


def local_counters() -> tuple[int, int] | None:
    try:
        import psutil
        sent = recv = 0
        for name, c in psutil.net_io_counters(pernic=True).items():
            if name.lower().startswith("lo") or "loopback" in name.lower():
                continue
            sent += c.bytes_sent; recv += c.bytes_recv
        return sent, recv
    except Exception:
        return None


class LinkMonitor:
    def __init__(self, settings, history=None):
        self.s, self.history = settings, history
        self.samples: deque[LinkSample] = deque(maxlen=1500)       # ~2 h at 5 s
        self.router = None
        self.router_error = ""
        self.cap_up: float | None = None
        self.cap_down: float | None = None
        self._stop = threading.Event()
        self._prev: dict[str, tuple[float, int, int]] = {}
        threading.Thread(target=self._run, daemon=True, name="link-monitor").start()

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------
    def connect_router(self):
        s = self.s
        try:
            if s.snmp_host and s.snmp_ifindex:
                self.router = snmp.SnmpRouter(s.snmp_host, s.snmp_community or "public", int(s.snmp_ifindex))
            elif s.upnp_enabled:
                self.router = upnp.find_router()
                if not self.router:
                    self.router_error = "No UPnP router found (enable UPnP on the router, or configure SNMP in Settings)"
            if self.router:
                self.router_error = ""
                up, dn = self.router.capacity()
                self.cap_up, self.cap_down = up, dn
        except Exception as e:
            self.router, self.router_error = None, str(e)
        if s.link_up_mbps:
            self.cap_up = s.link_up_mbps * 1e6

    def _rate(self, key: str, sent: int, recv: int, wrap: int, now: float):
        prev = self._prev.get(key)
        self._prev[key] = (now, sent, recv)
        if not prev or now - prev[0] <= 0:
            return None
        dt = now - prev[0]
        ds, dr = (sent - prev[1]) % wrap, (recv - prev[2]) % wrap       # handles counter wrap
        return ds * 8 / dt, dr * 8 / dt

    def _run(self):
        self.connect_router()
        last_try = time.time()
        while not self._stop.is_set():
            t0 = time.time()
            if not self.router and self.s.upnp_enabled and t0 - last_try > 300:
                last_try = t0
                self.connect_router()
            loc = local_counters()
            lr = self._rate("pc", *loc, 2 ** 64, t0) if loc else None
            rr = None
            if self.router:
                try:
                    rr = self._rate("router", *self.router.counters(), self.router.wrap, t0)
                except Exception as e:
                    self.router_error = f"router counters failed: {e}"
            if lr or rr:
                lup, ldn = lr or (0.0, 0.0)
                smp = LinkSample(t0, rr[0] if rr else None, rr[1] if rr else None, lup, ldn)
                self.samples.append(smp)
                if self.history:
                    self.history.add_link("pc", lup, ldn, t0)
                    if rr:
                        self.history.add_link("router", rr[0], rr[1], t0)
            self._stop.wait(max(0.5, INTERVAL - (time.time() - t0)))

    # ------------------------------------------------------------------
    @property
    def source(self) -> str:
        return self.router.name if self.router else "this PC only (no router counters)"

    def latest(self, seconds: float = 30):
        now = time.time()
        rows = [s for s in self.samples if s.ts >= now - seconds]
        if not rows:
            return None
        avg = lambda xs: sum(xs) / len(xs) if xs else None
        return dict(up=avg([s.up for s in rows if s.up is not None]), down=avg([s.down for s in rows if s.down is not None]),
                    lup=avg([s.lup for s in rows]), ldown=avg([s.ldown for s in rows]), n=len(rows))
