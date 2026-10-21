"""Pattern detection over drop events and link usage (the 'Plex2Shell / saturated upload' signatures)."""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from statistics import median, mean, pstdev


@dataclass
class Alert:
    key: str
    level: str          # warn | bad
    title: str
    detail: str = ""


@dataclass
class LinkSample:
    ts: float
    up: float | None        # router WAN upload, bits/s (None if no router source)
    down: float | None
    lup: float              # this PC's own upload, bits/s
    ldown: float


DROPS_PER_HOUR = 3
SAT_FRACTION = 0.8
ASYM_RATIO = 10.0


def analyze_drops(events, now: float | None = None, ongoing_since: float | None = None, target: str = "") -> list[Alert]:
    now = now or time.time()
    ev = sorted(events)
    pre = f"{target}: " if target else ""
    alerts: list[Alert] = []
    hour = [e for e in ev if e[1] >= now - 3600]
    n = len(hour) + (1 if ongoing_since else 0)
    if n > DROPS_PER_HOUR:
        dur = median(b - a for a, b in hour) if hour else 0
        gaps = [hour[i + 1][0] - hour[i][1] for i in range(len(hour) - 1)]
        gap = median(gaps) if gaps else None
        d = (f"{n} separate drops in the last hour (a drop = 3+ failed pings in a row, then recovery); more than "
             f"{DROPS_PER_HOUR}/hour is abnormal. Typical outage {dur:.0f} s"
             + (f", back up ~{gap / 60:.1f} min between drops" if gap else "") + ". ")
        recent = [e for e in ev if e[0] >= now - 3 * 3600]
        if len(recent) >= 4:
            iv = [recent[i + 1][0] - recent[i][0] for i in range(len(recent) - 1)]
            if mean(iv) > 0 and pstdev(iv) / mean(iv) < 0.6:
                d += f"The drops repeat on a regular cycle (about every {median(iv) / 60:.1f} min). "
        d += ("This stop-start pattern is typical of something saturating your upload link (router keep-alives starve and the ISP "
              "resets the session) or a flapping line – not a single ISP outage. Check the 'Drops & link' tab for upload usage and "
              "what on your network is sending data.")
        alerts.append(Alert(f"cyclic:{target}", "bad", f"{pre}Repeating connection drops ({n} in the last hour)", d))
    week = [e for e in ev if e[0] >= now - 7 * 86400]
    by_hour: dict[int, set] = defaultdict(set)
    for a, _ in week:
        t = time.localtime(a)
        by_hour[t.tm_hour].add((t.tm_year, t.tm_yday))
    hot = sorted(h for h, days in by_hour.items() if len(days) >= 3)
    if hot:
        days = max(len(by_hour[h]) for h in hot)
        alerts.append(Alert(f"tod:{target}", "warn", f"{pre}Drops cluster at the same time of day",
                            f"Drops happen around {', '.join(f'{h:02d}:00' for h in hot)} on up to {days} different days. Same-time "
                            "clustering points to a persistent background process or scheduled load on your network "
                            "rather than random ISP trouble."))
    return alerts


def analyze_link(samples: list[LinkSample], cap_up_bps: float | None = None, now: float | None = None) -> list[Alert]:
    now = now or time.time()
    alerts: list[Alert] = []
    rows = [s for s in samples if s.ts >= now - 300]
    if not rows:
        return alerts
    router = any(s.up is not None for s in rows)
    up = lambda s: s.up if s.up is not None else s.lup
    dn = lambda s: s.down if s.down is not None else s.ldown
    src = "your router's WAN link" if router else "this PC"
    last = [s for s in rows if s.ts >= now - 60]
    if cap_up_bps and len(last) >= 8 and all(up(s) >= SAT_FRACTION * cap_up_bps for s in last):
        u = mean(up(s) for s in last) / 1e6
        pc = mean(s.lup for s in last) / 1e6
        who = (f" Only {pc:.1f} Mbps of it comes from this PC, so another device on your network is sending the rest."
               if router and pc < 0.3 * u else "")
        alerts.append(Alert("link_saturated", "bad", f"Upload link saturated ({u:.0f} of {cap_up_bps / 1e6:.0f} Mbps)",
                            f"Upload on {src} has been above {SAT_FRACTION:.0%} of capacity for over a minute.{who} A saturated "
                            "upload starves router keep-alives and can cause connection drops. Most often this is something ordinary "
                            "(cloud backup, torrent, remote streaming, a big upload). If nothing you know of is uploading, it's also worth "
                            "checking for an unwanted device or software."))
    if len(rows) >= 50 and rows[-1].ts - rows[0].ts >= 240:
        u, d = mean(up(s) for s in rows), mean(dn(s) for s in rows)
        if u > 5e6 and u / max(d, 1.0) > ASYM_RATIO:
            alerts.append(Alert("link_asym", "bad" if u / max(d, 1) > 30 else "warn",
                                f"Upload is {u / max(d, 1):.0f}× download for 5 minutes ({u / 1e6:.1f} vs {d / 1e6:.2f} Mbps)",
                                f"Measured on {src}. Normal use is far more balanced; a lopsided flood of outgoing traffic is the "
                                "pattern of backups, torrents or remote streaming – and, less commonly, of a device being misused."))
    return alerts


def hour_histogram(events, since: float) -> list[int]:
    h = [0] * 24
    for a, _ in events:
        if a >= since:
            h[time.localtime(a).tm_hour] += 1
    return h
