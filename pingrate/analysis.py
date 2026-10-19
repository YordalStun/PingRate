"""Turns raw per-hop statistics into a plain-English verdict about where traffic is lost."""
from __future__ import annotations

from dataclasses import dataclass, field

from .stats import HopStats

WINDOW = 20        # most recent probes considered
MIN_SAMPLES = 5
LOSS_WARN = 5.0    # %
LOSS_BAD = 25.0


@dataclass
class Finding:
    level: str                      # ok | warn | bad | wait
    title: str
    detail: str = ""
    hop_status: dict[int, tuple[str, str]] = field(default_factory=dict)   # row index -> (code, label)
    origin_row: int | None = None


def _loss(h: HopStats) -> float | None:
    r = h.recent(WINDOW)
    if len(r) < MIN_SAMPLES:
        return None
    return 100.0 * sum(v is None for v in r) / len(r)


def _avg(h: HopStats) -> float | None:
    v = [x for x in h.recent(WINDOW) if x is not None]
    return sum(v) / len(v) if v else None


def _name(h: HopStats, n: int) -> str:
    who = h.host or h.addr or "unknown router"
    return f"hop {n} ({who})"


def _where(i: int, last: int) -> str:
    n = i + 1
    if i == last:
        return "at the destination itself"
    if n == 1:
        return "in your own router / local network (Wi‑Fi, cable or router)"
    if n <= 3:
        return "in your ISP's access network, just outside your home"
    return "somewhere along the internet path between your ISP and the destination"


def analyze_route(hops: list[HopStats], dest: HopStats, dest_ttl: int | None = None) -> Finding:
    rows = list(hops) + [dest]
    last = len(rows) - 1
    loss = [_loss(r) for r in rows]
    status: dict[int, tuple[str, str]] = {}
    if loss[-1] is None:
        return Finding("wait", "Collecting data…", "Need a few more probes before judging this route.")

    dest_loss = loss[-1]
    dest_bad = dest_loss >= LOSS_WARN

    for i, l in enumerate(loss):
        if l is None:
            status[i] = ("wait", "…")
        elif l < LOSS_WARN:
            status[i] = ("ok", "OK")
        else:
            status[i] = ("limited", "No/slow replies – ignorable") if l >= 99 and not dest_bad else ("limited", "Rate‑limited – ignorable")

    if not dest_bad:
        for i, l in enumerate(loss[:-1]):          # real loss would show up at the destination too
            if l is not None and l >= LOSS_WARN:
                status[i] = ("limited", "Router ignores pings – harmless" if l >= 99 else "Rate‑limited – harmless")
        f = _latency_finding(rows, status)
        if f:
            f.hop_status = status
            return f
        avg = _avg(dest)
        return Finding("ok", "Connection looks healthy",
                       f"Destination replies in {avg:.0f} ms with {dest_loss:.0f}% loss." if avg is not None else "",
                       status)

    # Real loss: find first hop from which every later hop is also affected.
    origin = last
    for i in range(last + 1):
        if loss[i] is None or loss[i] < LOSS_WARN:
            continue
        later = [l for l in loss[i + 1:] if l is not None]
        if all(l >= max(LOSS_WARN, loss[i] * 0.5) for l in later):
            origin = i
            break
    for i in range(last + 1):
        if i < origin:
            if loss[i] is not None and loss[i] >= LOSS_WARN:
                status[i] = ("limited", "Rate‑limited – harmless")
        elif i == origin:
            status[i] = ("lost", "LOSS STARTS HERE")
        else:
            status[i] = ("down", "Affected")

    level = "bad" if dest_loss >= LOSS_BAD else "warn"
    where = _where(origin, last)
    prev = rows[origin - 1] if origin > 0 else None
    if dest_loss >= 99:
        if origin == last:
            title = "Destination is not answering"
            detail = (f"The route is fine up to {_name(rows[origin - 1], origin)} but the destination itself "
                      f"does not reply. It may be down, offline, or blocking ping.") if prev else \
                "Nothing replies at all – check your network connection."
        else:
            title = f"Connection lost at {_name(rows[origin], origin + 1)}"
            detail = (f"Traffic stops here – nothing beyond it replies. "
                      + (f"Last good hop: {_name(prev, origin)}. " if prev else "")
                      + f"The fault is in {where}.")
    else:
        title = f"Packet loss ({dest_loss:.0f}%) starts at {_name(rows[origin], origin + 1)}"
        detail = (f"Hops before it are clean, from here on packets are dropped. "
                  f"The fault is in {where}.")
    return Finding(level, title, detail, status, origin)


def _latency_finding(rows: list[HopStats], status) -> Finding | None:
    avgs = [(i, _avg(r)) for i, r in enumerate(rows)]
    avgs = [(i, a) for i, a in avgs if a is not None]
    for (i0, a0), (i1, a1) in zip(avgs, avgs[1:]):
        jump = a1 - a0
        if jump >= 60 and (avgs[-1][1] - a0) >= jump * 0.6:
            status[i1] = ("slow", f"Latency +{jump:.0f} ms")
            return Finding("warn", f"Latency jumps +{jump:.0f} ms at hop {i1 + 1}",
                           f"Delay climbs between hop {i0 + 1} and hop {i1 + 1} and stays high to the destination – "
                           f"typically a congested or very long link there.", None, i1)
    d = avgs[-1][1] if avgs else None
    if d is not None and d >= 200:
        return Finding("warn", f"High latency ({d:.0f} ms)", "The whole path is slow; check whether the destination is far away.")
    return None
