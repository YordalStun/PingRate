"""Builds the 'full output' report: everything PingRate knows, written for a human or an AI to diagnose from."""
from __future__ import annotations

import dataclasses
import json
import platform
import subprocess
import sys
import time

from . import __version__

PREAMBLE = """\
PINGRATE FULL DIAGNOSTIC EXPORT
===============================
You are being given a network diagnostic export produced by PingRate (a Windows ping/route/diagnostics tool).
Please act as a network engineer: read ALL sections, then
  1. state the most likely root cause(s) of the user's connection problems and how confident you are,
  2. say WHERE the fault is (user's PC/Wi-Fi, home router, ISP, internet path, destination server),
  3. list concrete next steps in order of likelihood, and what extra data would settle any doubt.

How to read this file
- 'Loss' on a route hop only matters if it continues to later hops/destination. A middle hop with loss but a
  clean destination is just a router de-prioritising ICMP (harmless).
- Probe lists are oldest -> newest, in milliseconds; '-' means the probe got no reply.
- Two-way UDP test: a reply means the packet went out AND came back. UDP failing while TCP/web works is the
  classic 'I can see/hear others but they can't hear/see me' online-game symptom (blocked UDP / strict NAT / upstream issue).
- Echo-server test (if present) separates upload loss (you->server) from download loss (server->you).
- Section 'HISTORY' covers up to the last 7 days (per-minute data summarised hourly).
Privacy note: this file contains your IP addresses/adapter configuration. Remove anything you don't want to share.
"""


def _ts(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def _run(cmd: list[str], timeout=8) -> str:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags,
                           errors="replace")
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"(could not run {' '.join(cmd)}: {e})"


def system_info() -> list[tuple[str, str]]:
    out = [("Platform", platform.platform()), ("Python", sys.version.split()[0]), ("PingRate", __version__),
           ("Report time", _ts(time.time()))]
    if sys.platform == "win32":
        out += [("ipconfig /all", _run(["ipconfig", "/all"])),
                ("Wi-Fi interface (netsh wlan show interfaces)", _run(["netsh", "wlan", "show", "interfaces"])),
                ("IPv4 routing table (route print -4)", _run(["route", "print", "-4"]))]
    else:
        out += [("ip addr", _run(["ip", "-brief", "addr"])), ("ip route", _run(["ip", "route"]))]
    return out


def _f(v, d=1):
    return "-" if v is None else f"{v:.{d}f}"


def _probes(vals) -> str:
    return " ".join("-" if v is None else (f"{v:.0f}" if v >= 10 else f"{v:.1f}") for v in vals)


def _hist_range(hist, target, days, bucket):
    since = time.time() - days * 86400
    return since, hist.series(target, since, bucket)


def target_section(m, hist=None) -> tuple[str, dict]:
    f = m.finding()
    d = m.dest
    lines = [f"### Target: {m.target}  (IP {m.ip or 'unresolved'})", f"Verdict: [{f.level.upper()}] {f.title}"]
    if f.detail:
        lines.append(f"Detail: {f.detail}")
    if m.error:
        lines.append(f"Error: {m.error}")
    lines.append(f"Destination: sent {d.sent}, received {d.recv}, loss {d.loss_pct:.1f}%, last {_f(d.last)} ms, "
                 f"avg {_f(d.avg)}, best {_f(d.best)}, worst {_f(d.worst)}, jitter {_f(d.jitter)} ms")
    if m.fail_since:
        lines.append(f"Currently unresponsive for {m.outage_for:.0f} s")
    lines += ["", "Route (this session):",
              f"{'Hop':>3}  {'Address':<16} {'Host':<38} {'Status':<28} {'Loss':>6} {'Sent':>6} {'Last':>7} {'Avg':>7} {'Best':>7} {'Worst':>7} {'Jitter':>7}"]
    rows = list(m.hops) + [d]
    jrows = []
    for i, h in enumerate(rows):
        code, label = f.hop_status.get(i, ("", ""))
        lines.append(f"{h.ttl or i + 1:>3}  {(h.addr or '*'):<16} {(h.host or '')[:37]:<38} {label[:27]:<28} {h.loss_pct:>5.0f}% {h.sent:>6} "
                     f"{_f(h.last):>7} {_f(h.avg):>7} {_f(h.best):>7} {_f(h.worst):>7} {_f(h.jitter):>7}")
        jrows.append(dict(hop=h.ttl or i + 1, addr=h.addr, host=h.host, status=label, loss=round(h.loss_pct, 1), sent=h.sent,
                          avg=h.avg, best=h.best, worst=h.worst, jitter=h.jitter, is_destination=i == len(rows) - 1))
    lines += ["", "Last 300 destination probes (ms, '-' = lost):", _probes(d.recent(300)), "", "Last 60 probes per hop:"]
    for i, h in enumerate(rows):
        lines.append(f"  hop {h.ttl or i + 1:>2} {(h.addr or '*'):<16} {_probes(h.recent(60))}")
    jd = dict(target=m.target, ip=m.ip, verdict=dict(level=f.level, title=f.title, detail=f.detail), route=jrows,
              recent_destination=d.recent(300))
    if hist:
        lines += ["", *history_lines(hist, m.target)]
    return "\n".join(lines), jd


def history_lines(hist, target) -> list[str]:
    now = time.time()
    since7 = now - 7 * 86400
    s = hist.summary(target, since7)
    out = [f"HISTORY for {target} (last 7 days)"]
    if not s["sent"]:
        return out + ["  (no stored history yet)"]
    cover = s["minutes"] / (7 * 1440) * 100
    out.append(f"  {s['sent']} probes in {s['minutes']} monitored minutes (~{cover:.0f}% of the week); loss {s['loss']:.2f}%, "
               f"avg {_f(s['avg'])} ms, min {_f(s['min'])}, max {_f(s['max'])}")
    outs = hist.outages(target, since7)
    out.append(f"  Outages (>=50% loss for a minute or more): {len(outs)}")
    for o in outs[-40:]:
        out.append(f"    {_ts(o['start'])} -> {_ts(o['end'])}  ({(o['end'] - o['start']) / 60:.0f} min, {o['loss']:.0f}% loss)")
    out.append("  Per-hop loss over the week (a hop whose loss carries on to the destination is the real fault):")
    for h in hist.hop_loss(target, since7):
        out.append(f"    hop {('dest' if h['ttl'] == 0 else h['ttl']):>4} {(h['addr'] or '*'):<16} loss {h['loss']:5.1f}%  avg {_f(h['avg'])} ms  ({h['sent']} probes)")
    out.append("  Hourly (time, loss %, avg ms, max ms):")
    for r in hist.series(target, since7, 3600):
        out.append(f"    {_ts(r['t'])}  loss {r['loss']:5.1f}%  avg {_f(r['avg']):>6}  max {_f(r['max']):>6}")
    return out


def diag_section(results: dict | None, verdict) -> tuple[str, list]:
    if not results:
        return "(no diagnostics run in this session)", []
    lines = []
    for r in results.values():
        lines.append(f"[{r.status.upper():4}] {r.name}: {r.summary}")
        lines += [f"         {x}" for x in r.details]
        if r.data:
            lines.append("         data: " + json.dumps({k: (sorted(v) if isinstance(v, set) else v) for k, v in r.data.items()},
                                                          default=str))
    lines += ["", "Automatic conclusions:"] + [f"  [{lvl}] {txt}" for lvl, txt in verdict]
    from .diag.tests import causes
    cs = causes(results)
    if cs:
        lines += ["", "Possible contributing causes (ranked; most are everyday causes):"]
        for i, c in enumerate(cs, 1):
            lines += [f"  {i}. {c['title']}", f"       why: {c['why']}", f"       try: {c['fix']}"]
    return "\n".join(lines), [dataclasses.asdict(r) for r in results.values()]


def incident_lines(monitors, link, hist) -> list[str]:
    from . import incidents
    out = ["A 'drop' = 3+ consecutive failed pings followed by recovery; >3 per hour is flagged as abnormal."]
    now = time.time()
    for m in monitors:
        ev = m.events(now - 7 * 86400)
        out.append(f"{m.target}: {len(ev)} drops in 7 days; last hour: {sum(1 for e in ev if e[1] >= now - 3600)}"
                   + (f"; DOWN right now since {_ts(m.ongoing_drop_since)}" if m.ongoing_drop_since else ""))
        for a in incidents.analyze_drops(ev, now, m.ongoing_drop_since, m.target):
            out.append(f"  [{a.level}] {a.title}: {a.detail}")
        out.append("  drops by hour of day (00..23): " + " ".join(str(x) for x in incidents.hour_histogram(ev, now - 7 * 86400)))
        for a_, b_ in ev[-30:]:
            out.append(f"    {_ts(a_)} -> {_ts(b_)} ({b_ - a_:.0f}s)")
    if link:
        lat = link.latest(60)
        out.append(f"Link source: {link.source}" + (f"; upload capacity {link.cap_up / 1e6:.0f} Mbps" if link.cap_up else ""))
        if lat:
            f = lambda v: "n/a" if v is None else f"{v / 1e6:.2f}"
            out.append(f"Last minute (Mbps): router up {f(lat['up'])} down {f(lat['down'])} | this PC up {f(lat['lup'])} down {f(lat['ldown'])}")
        for a in incidents.analyze_link(list(link.samples), link.cap_up):
            out.append(f"  [{a.level}] {a.title}: {a.detail}")
        if link.router_error:
            out.append(f"Router counters: {link.router_error}")
    if hist:
        for src in hist.link_sources():
            ser = hist.link_series(src, now - 86400, 3600)
            out.append(f"Link usage last 24h, hourly avg Mbps ({src}): " + "  ".join(
                f"{time.strftime('%H:%M', time.localtime(r['t']))} up {r['up'] / 1e6:.1f}/down {r['down'] / 1e6:.1f}" for r in ser))
    return out


def build_report(settings, monitors, diag_results=None, diag_verdict=None, hist=None, system=True, link=None) -> str:
    parts = [PREAMBLE]
    jtargets = []
    if system:
        parts += ["=" * 70, "SYSTEM", "=" * 70]
        for k, v in system_info():
            parts.append(f"--- {k}\n{v.strip()}" if "\n" in v else f"{k}: {v}")
    parts += ["", "=" * 70, "SETTINGS", "=" * 70, json.dumps(dataclasses.asdict(settings), indent=2)]
    parts += ["", "=" * 70, "LIVE MONITORING (current session)", "=" * 70]
    for m in monitors:
        text, j = target_section(m, hist)
        parts += [text, ""]
        jtargets.append(j)
    if hist:
        known = {m.target for m in monitors}
        for t in hist.targets():
            if t not in known:
                parts += [f"(target no longer monitored: {t})", *history_lines(hist, t), ""]
    parts += ["=" * 70, "INCIDENT SIGNALS (drop patterns, link load)", "=" * 70, *incident_lines(monitors, link, hist), ""]
    parts += ["=" * 70, "DIAGNOSTICS (latest run)", "=" * 70]
    dtext, dj = diag_section(diag_results, diag_verdict or [])
    parts.append(dtext)
    if hist:
        runs = hist.diag_runs(10)
        if runs:
            parts += ["", "Earlier diagnostics runs (newest first):"]
            for r in runs:
                v = "; ".join(f"[{l}] {t}" for l, t in r["verdict"])
                parts.append(f"  {_ts(r['ts'])}  {r['reason'] or 'manual'}")
                parts.append(f"      {v[:600]}")
                bad = [x for x in r["results"] if x.get("status") in ("fail", "warn")]
                parts += [f"      {x['status'].upper():4} {x['name']}: {x['summary']}" for x in bad]
    if hist:
        from .diag.tests import METRIC_INFO
        parts += ["", "=" * 70, "TEST METRIC HISTORY (every diagnostics run: time=value, oldest -> newest, up to 300 points each)", "=" * 70]
        for k in hist.metric_keys():
            lab, unit = METRIC_INFO.get(k, (k, ""))
            ser = hist.metric_series(k)[-300:]
            parts.append(f"{lab} [{k}, {unit}] ({len(ser)} runs): " +
                         "  ".join(f"{time.strftime('%m-%d %H:%M', time.localtime(t))}={v:.1f}" for t, v in ser))
        if not hist.metric_keys():
            parts.append("(no completed diagnostics runs stored yet)")
    parts += ["", "=" * 70, "MACHINE-READABLE SUMMARY (JSON)", "=" * 70,
              json.dumps(dict(version=__version__, time=_ts(time.time()), targets=jtargets, diagnostics=dj,
                              conclusions=diag_verdict or []), indent=1, default=str)]
    return "\n".join(parts) + "\n"
