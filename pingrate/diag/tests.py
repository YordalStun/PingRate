"""Diagnostic test suite + plain-English verdict."""
from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field

from .. import icmp
from . import net

PASS, WARN, FAIL, SKIP = "pass", "warn", "fail", "skip"
STUN_SERVERS = [("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478), ("stun1.l.google.com", 19302)]


@dataclass
class Result:
    key: str
    name: str
    status: str = SKIP
    summary: str = ""
    details: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


@dataclass
class Context:
    settings: object
    targets: list = field(default_factory=list)     # [(name, level, title, detail)]
    cancel: threading.Event = field(default_factory=threading.Event)
    progress: object = lambda msg: None
    link: object = None                             # LinkMonitor
    drops: list = field(default_factory=list)       # [(target, events, ongoing_since)]
    skip: set = field(default_factory=set)         # test keys to skip, e.g. {'speed','video'}
    results: dict = field(default_factory=dict)


def _pings(ip, n=10, ttl=128, size=32, gap=0.1):
    import time
    out = []
    for _ in range(n):
        r = icmp.ping(ip, ttl, 1000, size)
        out.append(r.rtt if r.kind == icmp.OK else None)
        time.sleep(gap)
    return out


def _stat(v):
    ok = [x for x in v if x is not None]
    loss = 100 * (len(v) - len(ok)) / len(v)
    return loss, (statistics.mean(ok) if ok else None), (max(ok) if ok else None)


# --------------------------------------------------------------------------- tests
def t_gateway(c: Context) -> Result:
    r = Result("gateway", "Local network (router)")
    c.progress("Finding your router…")
    gw = None
    for ip in ("1.1.1.1", "8.8.8.8"):
        rep = icmp.ping(ip, 1, 1500)
        if rep.addr and rep.kind == icmp.TTL_EXPIRED:
            gw = rep.addr
            break
    if not gw:
        r.status, r.summary = FAIL, "Could not reach your router at all"
        r.details = ["No router answered. Check the cable / Wi‑Fi connection or restart the router."]
        return r
    loss, avg, worst = _stat(_pings(gw, 15, 128, gap=0.05))
    r.data = {"gw": gw, "loss": loss, "avg": avg}
    r.summary = f"Router {gw}: {loss:.0f}% loss" + (f", avg {avg:.1f} ms, worst {worst:.0f} ms" if avg else "")
    if loss >= 99:
        r.status = FAIL
        r.details = ["Your router is not answering pings. Either it blocks them or the link to it (Wi‑Fi/cable) is down."]
    elif loss > 2 or (avg and avg > 20):
        r.status = WARN
        r.details = ["Unstable link to your own router – try a wired connection or move closer to the Wi‑Fi."]
    else:
        r.status = PASS
    return r


def t_internet(c: Context) -> Result:
    r = Result("internet", "Internet reachability (ping)")
    bad, parts, losses, avgs = 0, [], [], []
    for ip in ("1.1.1.1", "8.8.8.8", "9.9.9.9"):
        if c.cancel.is_set():
            break
        c.progress(f"Pinging {ip}…")
        loss, avg, _ = _stat(_pings(ip, 10, gap=0.05))
        parts.append(f"{ip}: {loss:.0f}% loss" + (f", {avg:.0f} ms" if avg else ""))
        bad += loss >= 99
        losses.append(loss)
        if avg:
            avgs.append(avg)
    avg_loss = sum(losses) / len(losses) if losses else None
    avg_ms = sum(avgs) / len(avgs) if avgs else None
    r.details = parts
    r.data = {"all_down": bad == 3, "loss": avg_loss, "avg": avg_ms}
    r.status = FAIL if bad == 3 else WARN if bad else PASS
    r.summary = "No internet servers answer" if bad == 3 else "Some servers do not answer" if bad else "Internet servers reachable"
    return r


def t_dns(c: Context) -> Result:
    r = Result("dns", "DNS lookups")
    c.progress("Testing DNS…")
    names = ["google.com", "cloudflare.com", "wikipedia.org"]
    sysms = [net.system_dns(n) for n in names]
    r.details.append("Your system resolver: " + ", ".join(f"{n} {'%.0f ms' % m if m else 'FAILED'}" for n, m in zip(names, sysms)))
    udp_fail = 0
    for srv in ("1.1.1.1", "8.8.8.8"):
        m = [net.dns_query(srv, n) for n in names]
        udp_fail += all(x is None for x in m)
        r.details.append(f"Direct to {srv}: " + ", ".join(f"{'%.0f ms' % x if x else 'FAILED'}" for x in m))
    sys_fail = sum(m is None for m in sysms)
    ok = [m for m in sysms if m]
    r.data = {"sys_fail": sys_fail, "udp_fail": udp_fail, "avg": statistics.mean(ok) if ok else None}
    if sys_fail == 3:
        r.status, r.summary = FAIL, "System DNS is not working"
    elif sys_fail:
        r.status, r.summary = WARN, "Some DNS lookups fail"
    elif ok and statistics.mean(ok) > 300:
        r.status, r.summary = WARN, f"DNS is slow ({statistics.mean(ok):.0f} ms)"
    else:
        r.status, r.summary = PASS, f"DNS fine ({statistics.mean(ok):.0f} ms avg)"
    return r


def t_tcp(c: Context) -> Result:
    r = Result("tcp", "TCP connections")
    c.progress("Testing TCP connections…")
    hosts = [("1.1.1.1", 443), ("8.8.8.8", 443), ("steamcommunity.com", 443), ("discord.com", 443),
             ("xboxlive.com", 443), ("playstation.com", 443)]
    ok = 0
    for h, p in hosts:
        m = net.tcp_connect(h, p)
        ok += m is not None
        r.details.append(f"{h}:{p} – " + (f"{m:.0f} ms" if m else "FAILED"))
    r.data = {"ok": ok}
    r.status = FAIL if ok == 0 else WARN if ok < len(hosts) - 1 else PASS
    r.summary = f"{ok}/{len(hosts)} connections succeeded"
    return r


def t_udp(c: Context) -> Result:
    r = Result("udp", "Two-way UDP (game traffic)")
    servers = []
    for h, p in STUN_SERVERS:
        a = net.stun_resolve(h, p)
        if a:
            servers.append((h, a))
    if not servers:
        r.status, r.summary = FAIL, "Could not look up any UDP test server"
        return r
    tot_s = tot_r = 0
    for h, a in servers[:2]:
        if c.cancel.is_set():
            break
        c.progress(f"UDP burst to {h}…")
        b = net.stun_burst(a, 40)
        tot_s += b["sent"]; tot_r += b["recv"]
        rt = f", avg {statistics.mean(b['rtts']):.0f} ms" if b["rtts"] else ""
        r.details.append(f"{h}: sent {b['sent']}, got {b['recv']} replies{rt}")
    # fresh source ports
    import socket
    c.progress("Testing different source ports…")
    ok_ports = 0
    a = servers[0][1]
    for _ in range(12):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ok_ports += net.stun_burst(a, 1, 0.0, s, wait=0.8)["recv"]
        s.close()
    r.details.append(f"Fresh source ports that worked: {ok_ports}/12")
    # NAT type
    nat = "unknown"
    if len(servers) >= 2:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        m1 = net.stun_burst(servers[0][1], 1, 0.0, s, wait=1.0)["mapped"]
        m2 = net.stun_burst(servers[1][1], 1, 0.0, s, wait=1.0)["mapped"]
        s.close()
        if m1 and m2:
            nat = "open" if m1 == m2 else "strict"
            r.details.append(f"Public address seen: {sorted(m1)[0][0]}  (ports {sorted(m1)[0][1]} vs {sorted(m2)[0][1]})")
            r.details.append("NAT type: " + ("friendly (same port for every server)" if nat == "open"
                             else "STRICT / symmetric (port changes per destination – bad for multiplayer)"))
    loss = 100 * (tot_s - tot_r) / tot_s if tot_s else 100
    r.data = {"loss": loss, "recv": tot_r, "ports_ok": ok_ports, "nat": nat}
    if tot_r == 0:
        r.status, r.summary = FAIL, "UDP replies never come back – UDP is blocked or one direction is broken"
    elif loss > 10:
        r.status, r.summary = FAIL, f"{loss:.0f}% of UDP packets lost (out or back)"
    elif loss > 2 or ok_ports < 11:
        r.status, r.summary = WARN, f"UDP mostly works ({loss:.0f}% lost; {ok_ports}/12 ports)"
    elif nat == "strict":
        r.status, r.summary = WARN, "UDP works but your NAT is strict"
    else:
        r.status, r.summary = PASS, f"UDP works both ways ({tot_r}/{tot_s} replies)"
    r.details.append("A reply means that packet travelled out AND back. Lost packets can't be assigned to a "
                     "direction without the optional echo server.")
    return r


def t_echo(c: Context) -> Result:
    r = Result("echo", "Direction test (echo server)")
    addr = (c.settings.echo_server or "").strip()
    if not addr:
        r.summary = "Not configured – add an echo server in Settings to measure each direction separately"
        return r
    host, _, port = addr.partition(":")
    c.progress(f"Echo server test with {addr}…")
    e = net.echo_test(host, int(port or 40404))
    if not e:
        r.status, r.summary = FAIL, "Could not resolve the echo server"
        return r
    sent, srv, cli = e["sent"], e["server_recv"], e["client_recv"]
    if srv is None or (srv == 0 and cli == 0):
        r.status, r.summary = FAIL, "Echo server never answered (server down, port closed, or your UDP is blocked)"
        r.data = {"up_loss": 100.0, "down_loss": 100.0}
        return r
    up = 100 * (sent - srv) / sent
    down = 100 * (srv - cli) / srv if srv else 100.0
    r.data = {"up_loss": up, "down_loss": down}
    r.details = [f"You sent {sent} packets → server received {srv}  (upload loss {up:.0f}%)",
                 f"Server answered {srv} → you received {cli}  (download loss {down:.0f}%)"]
    worst = max(up, down)
    r.status = FAIL if worst > 10 else WARN if worst > 2 else PASS
    r.summary = f"Upload (you→server) loss {up:.0f}%, download (server→you) loss {down:.0f}%"
    return r


def t_websites(c: Context) -> Result:
    r = Result("web", "Websites actually load")
    good = 0
    times = []
    sites = c.settings.websites
    for u in sites:
        if c.cancel.is_set():
            break
        c.progress(f"Loading {u}…")
        p = net.http_probe(u)
        if p.get("ok"):
            good += 1
            times.append(p["total"])
            r.details.append(f"✔ {u} – HTTP {p['status']}, {p['bytes'] // 1024} KB, DNS {p['dns']:.0f} / connect "
                             f"{p['connect']:.0f} / TLS {p['tls']:.0f} / first byte {p['ttfb']:.0f} ms, total {p['total']:.0f} ms")
        else:
            r.details.append(f"✘ {u} – {p.get('error') or 'HTTP ' + str(p.get('status'))}")
    r.data = {"good": good, "n": len(sites), "avg": statistics.mean(times) if times else None}
    r.status = FAIL if good == 0 else WARN if good < len(sites) else PASS
    r.summary = f"{good}/{len(sites)} websites loaded completely"
    return r


def t_captive(c: Context) -> Result:
    r = Result("captive", "Captive portal / hijack check")
    p = net.http_probe("http://connectivitycheck.gstatic.com/generate_204")
    if p.get("status") == 204 or (p.get("error") is None and p.get("status") == 204):
        r.status, r.summary = PASS, "Normal internet access (no login page in the way)"
    elif p.get("status") in (200, 302, 301) or "empty" in str(p.get("error", "")):
        r.status = WARN if p.get("status") != 204 and p.get("status") else PASS
        r.summary = "Unexpected response – a login page or filter may be intercepting traffic"
        if "empty" in str(p.get("error", "")):          # 204 has an empty body: that's the success case
            r.status, r.summary = PASS, "Normal internet access (no login page in the way)"
    else:
        r.status, r.summary = WARN, f"Check failed: {p.get('error') or p.get('status')}"
    return r


def t_mtu(c: Context) -> Result:
    r = Result("mtu", "Packet size (MTU)")
    c.progress("Probing packet sizes…")
    best = None
    for size in (1472, 1452, 1400, 1300, 1200, 1000, 576):
        rep = icmp.ping("1.1.1.1", 128, 1500, size, True)
        if rep.kind == icmp.OK:
            best = size
            break
    if best is None:
        r.status, r.summary = WARN, "Could not determine MTU (large pings blocked)"
    elif best >= 1472:
        r.status, r.summary = PASS, "Full 1500-byte packets pass without fragmentation"
    else:
        r.status, r.summary = WARN, f"Largest unfragmented payload is {best} bytes (path MTU ≈ {best + 28})"
        r.details = ["Reduced MTU (e.g. PPPoE/VPN) is usually fine but can cause stalls on some sites/games."]
    return r


def t_speed(c: Context) -> Result:
    r = Result("speed", "Speed test")
    idle = [x for x in _pings("1.1.1.1", 8, gap=0.05) if x]
    d = net.speed_phase("down", 8, 4, c.progress)
    if c.cancel.is_set():
        return r
    u = net.speed_phase("up", 8, 3, c.progress)
    base = statistics.mean(idle) if idle else None
    def bloat(ph):
        return statistics.mean(ph["latency"]) - base if (base and ph["latency"]) else None
    r.data = {"down": d["mbps"], "up": u["mbps"], "bloat_down": bloat(d), "bloat_up": bloat(u)}
    r.details = [f"Download: {d['mbps']:.1f} Mbps", f"Upload: {u['mbps']:.1f} Mbps"]
    if base:
        for lbl, ph in (("download", d), ("upload", u)):
            if ph["latency"]:
                r.details.append(f"Ping under {lbl} load: {statistics.mean(ph['latency']):.0f} ms avg, "
                                 f"{max(ph['latency']):.0f} ms worst (idle {base:.0f} ms)")
    for e in (d["errors"] + u["errors"])[:2]:
        r.details.append(f"error: {e}")
    if d["mbps"] < 0.5 and u["mbps"] < 0.5:
        r.status, r.summary = FAIL, "Speed test could not transfer data"
    elif u["mbps"] < 0.5 or (d["mbps"] > 20 and u["mbps"] < d["mbps"] * 0.02):
        r.status, r.summary = FAIL, f"Download {d['mbps']:.0f} Mbps but upload only {u['mbps']:.1f} Mbps"
    else:
        r.status, r.summary = PASS, f"Download {d['mbps']:.0f} Mbps, upload {u['mbps']:.0f} Mbps"
        b = max(x for x in (r.data["bloat_down"], r.data["bloat_up"], 0) if x is not None)
        if b > 100:
            r.status = WARN
            r.summary += f" – but latency rises +{b:.0f} ms under load (bufferbloat)"
    return r


def t_video(c: Context) -> Result:
    r = Result("video", "Video streaming")
    urls = [c.settings.video_url] if c.settings.video_url else net.VIDEO_URLS
    for u in urls:
        if c.cancel.is_set():
            break
        c.progress("Loading test video…")
        v = net.video_test(u, 15, 5.0, c.progress)
        if v["loaded"]:
            r.data = v
            r.details = [f"Source: {u}", f"Started playing after {v['startup']:.1f} s",
                         f"Buffering stalls (simulated 1080p / 5 Mbps): {v['stalls']}, total {v['stall_time']:.1f} s frozen",
                         f"Data gaps over 0.5 s: {v['gaps']}", f"Average throughput {v['mbps']:.1f} Mbps"]
            if v["stalls"] == 0 and v["startup"] < 4 and v["gaps"] <= 1:
                r.status, r.summary = PASS, f"Plays smoothly (started in {v['startup']:.1f} s)"
            elif v["stalls"] <= 2:
                r.status, r.summary = WARN, f"Video stutters ({v['stalls']} stall(s), {v['stall_time']:.1f} s frozen)"
            else:
                r.status, r.summary = FAIL, f"Video would buffer constantly ({v['stalls']} stalls)"
            return r
        r.details.append(f"✘ {u} – {v.get('error', 'did not start')}")
    r.status, r.summary = FAIL, "No test video could be loaded"
    return r



def t_drops(c: Context) -> Result:
    from .. import incidents
    r = Result("drops", "Repeating-drop pattern")
    if not c.drops:
        r.summary = "No monitored targets"
        return r
    alerts, worst1h = [], 0
    for target, events, ongoing in c.drops:
        alerts += incidents.analyze_drops(events, None, ongoing, target)
        now = time.time()
        worst1h = max(worst1h, sum(1 for e in events if e[1] >= now - 3600) + (1 if ongoing else 0))
        r.details.append(f"{target}: {len(events)} drop(s) recorded in the last 7 days"
                         + (" – currently DOWN" if ongoing else ""))
    r.data = {"drops_1h": worst1h}
    for a in alerts:
        r.details.append(f"[{a.level}] {a.title}: {a.detail}")
    r.status = FAIL if any(a.level == "bad" for a in alerts) else WARN if alerts else PASS
    r.summary = alerts[0].title if alerts else f"No abnormal drop pattern (max {worst1h} drop(s) in the last hour)"
    return r


def t_link(c: Context) -> Result:
    from .. import incidents
    r = Result("link", "Upload / download load")
    lm = c.link
    if lm is None:
        r.summary = "Link monitor not running"
        return r
    c.progress("Reading link usage…")
    lat = lm.latest(60)
    if not lat:
        r.summary = "Still collecting link samples – try again in a minute"
        return r
    mb = lambda v: "n/a" if v is None else f"{v / 1e6:.2f} Mbps"
    r.details.append(f"Source: {lm.source}")
    if lm.cap_up:
        r.details.append(f"Upload capacity: {lm.cap_up / 1e6:.0f} Mbps" + (" (from settings)" if lm.s.link_up_mbps else " (reported by router)"))
    if lm.router_error:
        r.details.append(f"Router counters: {lm.router_error}")
    r.details.append(f"Last minute – router WAN: up {mb(lat['up'])}, down {mb(lat['down'])};  this PC: up {mb(lat['lup'])}, down {mb(lat['ldown'])}")
    up = lat["up"] if lat["up"] is not None else lat["lup"]
    dn = lat["down"] if lat["down"] is not None else lat["ldown"]
    r.data = {"up": up / 1e6, "down": dn / 1e6, "pc_up": lat["lup"] / 1e6}
    alerts = incidents.analyze_link(list(lm.samples), lm.cap_up)
    for a in alerts:
        r.details.append(f"[{a.level}] {a.title}: {a.detail}")
    if not lm.router:
        r.details.append("Tip: without router counters only this PC's own traffic is visible. Enable UPnP on the router or set SNMP in Settings "
                         "to see traffic from every device (e.g. a Plex server).")
    r.status = FAIL if any(a.level == "bad" for a in alerts) else WARN if alerts else PASS
    r.summary = alerts[0].title if alerts else f"Normal load (up {mb(up)}, down {mb(dn)})"
    return r


def t_plex(c: Context) -> Result:
    from .. import plex
    r = Result("plex", "Plex servers on your LAN (Plex2Shell)")
    if not c.settings.plex_scan:
        r.summary = "LAN scan disabled in Settings"
        return r
    servers, ip = plex.scan_lan(c.progress)
    if ip is None:
        r.status, r.summary = WARN, "Could not determine your local network"
        return r
    if not servers:
        r.status, r.summary = PASS, "No Plex server found on your local network"
        return r
    bad = 0
    for sv in servers:
        v = sv["vulnerable"]
        bad += bool(v)
        r.details.append(f"{sv['host']}: Plex {sv['version'] or 'unknown version'} – "
                         + ("VULNERABLE (fixed in 1.43.3.10861)" if v else "patched" if v is False else "version unknown"))
    r.data = {"vulnerable": bad}
    r.status = FAIL if bad else PASS
    r.summary = (f"{bad} vulnerable Plex server(s) – update to 1.43.3.10861+" if bad else f"{len(servers)} Plex server(s), all patched")
    return r


RISKY_PORTS = {22: "SSH", 23: "Telnet", 445: "SMB", 3389: "Remote Desktop", 5900: "VNC", 32400: "Plex"}


def t_exposure(c: Context) -> Result:
    r = Result("exposure", "Ports forwarded to the internet (UPnP)")
    lm = c.link
    router = getattr(lm, "router", None) if lm else None
    if not router or not hasattr(router, "port_mappings"):
        r.summary = "Router doesn't expose UPnP port-mapping info – check the router's port-forwarding page for 32400 (Plex)"
        return r
    c.progress("Reading port forwards from router…")
    try:
        maps = router.port_mappings()
    except Exception as e:
        r.status, r.summary = WARN, f"Could not read port mappings: {e}"
        return r
    ext = router.external_ip()
    if ext:
        r.details.append(f"Router's public IP: {ext}")
    plex_open = [m for m in maps if m["ext_port"] == 32400 or m["int_port"] == 32400]
    risky = [m for m in maps if m["int_port"] in RISKY_PORTS and m not in plex_open]
    for m in maps:
        r.details.append(f"{m['proto']} {m['ext_port']} → {m['client']}:{m['int_port']}  {m['desc']}" + ("" if m["enabled"] else "  (disabled)"))
    r.data = {"mappings": len(maps), "plex_open": len(plex_open)}
    if plex_open:
        r.status = WARN
        r.summary = f"Plex port 32400 is forwarded to the internet (→ {plex_open[0]['client']})"
    elif risky:
        r.status, r.summary = WARN, f"{len(risky)} sensitive port(s) forwarded ({', '.join(RISKY_PORTS[m['int_port']] for m in risky)})"
    else:
        r.status, r.summary = PASS, f"{len(maps)} port forward(s), none sensitive" if maps else "No ports forwarded via UPnP"
    return r


def t_ioc(c: Context) -> Result:
    from .. import ioc
    r = Result("ioc", "Known attacker infrastructure (this PC)")
    c.progress("Checking connections against known-bad IPs…")
    lst = ioc.load()
    hits = ioc.check_connections(lst) + ioc.check_dns_cache(lst)
    r.details.append(f"Checked {len(lst['ips'])} IPs and {len(lst['domains'])} domains (add your own in %APPDATA%\\PingRate\\iocs.json)")
    for what, why in hits:
        r.details.append(f"MATCH: {what} – {why}")
    r.data = {"matches": len(hits)}
    r.status = FAIL if hits else PASS
    r.summary = f"{len(hits)} match(es) – this PC is talking to known attacker infrastructure" if hits else "No connections to known attacker IPs/domains"
    return r


def t_targets(c: Context) -> Result:
    r = Result("targets", "Your monitored targets")
    if not c.targets:
        return r
    worst = PASS
    for name, level, title, detail in c.targets:
        r.details.append(f"{name}: {title}" + (f" – {detail}" if detail and level != 'ok' else ""))
        if level == "bad":
            worst = FAIL
        elif level == "warn" and worst == PASS:
            worst = WARN
    r.status = worst
    r.summary = "All targets healthy" if worst == PASS else "Problems on monitored targets (see route view)"
    return r


TESTS = [t_gateway, t_internet, t_dns, t_tcp, t_udp, t_echo, t_websites, t_captive, t_mtu, t_speed, t_video,
         t_drops, t_link, t_plex, t_exposure, t_ioc, t_targets]


def run_all(c: Context, on_result=lambda r: None) -> list[Result]:
    out = []
    for fn in TESTS:
        if c.cancel.is_set():
            break
        try:
            if fn.__name__[2:] in c.skip:
                res = Result(fn.__name__[2:], fn.__name__[2:].title(), SKIP, "Skipped in scheduled runs (enable in Settings)")
            else:
                res = fn(c)
        except Exception as e:                        # a broken test must not kill the run
            res = Result(fn.__name__, fn.__name__[2:], WARN, f"Test crashed: {e}")
        c.results[res.key] = res
        out.append(res)
        on_result(res)
    return out


# --------------------------------------------------------------------------- verdict
def verdict(results: dict[str, Result]) -> list[tuple[str, str]]:
    """List of (level, sentence) conclusions, most important first."""
    g = lambda k: results.get(k)
    st = lambda k: g(k).status if g(k) else SKIP
    out: list[tuple[str, str]] = []
    if st("tcp") == FAIL:
        if st("gateway") == FAIL:
            out.append(("bad", "The problem is inside your home network: your router can't be reached and nothing gets out. "
                               "Check the cable/Wi‑Fi, then restart the router."))
        else:
            out.append(("bad", "Your router works but nothing beyond it does – the fault is your modem/ISP connection. "
                               "Restart the modem; if it persists, contact your ISP."))
        return out
    if st("gateway") == WARN:
        out.append(("warn", "The link to your own router is unstable (Wi‑Fi interference or a bad cable) – try wired."))
    if st("internet") == FAIL and st("tcp") != FAIL:
        out.append(("warn", "Pings are blocked/unanswered but TCP connections work – the internet itself is up."))
    if st("dns") in (FAIL, WARN) and st("internet") != FAIL:
        out.append(("bad" if st("dns") == FAIL else "warn",
                    "DNS (name lookup) is failing or slow while the connection is alive. Set your DNS to 1.1.1.1 / 8.8.8.8."))
    udp, tcp, up = g("udp"), st("tcp"), g("speed")
    if udp and udp.status == FAIL and tcp != FAIL:
        if udp.data.get("recv") == 0:
            out.append(("bad", "TCP (web) works but UDP replies never return. Online games use UDP, so this matches 'I can "
                               "see/hear others but they can't hear/see me': your router/firewall/ISP is blocking or not "
                               "NAT-ing UDP. Try: disable Windows Firewall/third-party AV briefly, enable UPnP on the router, "
                               "restart the router, or put the PC in the router's DMZ."))
        else:
            out.append(("bad", f"UDP traffic is lossy ({udp.data.get('loss', 0):.0f}% lost) while web traffic works – expect "
                               "rubber-banding and voice dropouts in games."))
    elif udp and udp.status == WARN:
        out.append(("warn", "UDP is partly unreliable or your NAT is strict – multiplayer may fail to connect or one-way "
                            "audio can occur. Enable UPnP / forward ports / use a wired connection."))
    e = g("echo")
    if e and e.status in (FAIL, WARN) and e.data:
        if e.data["up_loss"] > e.data["down_loss"] + 5:
            out.append(("bad", f"Upload direction is the problem: {e.data['up_loss']:.0f}% of packets you send never "
                               "arrive, while the return path is fine. That's why others can reach you but you can't reach them."))
        elif e.data["down_loss"] > e.data["up_loss"] + 5:
            out.append(("bad", f"Download direction is the problem: {e.data['down_loss']:.0f}% of packets coming back are lost."))
    if up and up.status == FAIL and up.data.get("up", 1) < 0.5 <= up.data.get("down", 0) + 0.5:
        out.append(("bad", "Upload is essentially dead while download works – upstream is faulty (modem/line/ISP)."))
    elif up and up.status == FAIL:
        out.append(("bad", "Upload is far slower than download – upstream link is struggling."))
    if up and up.data.get("bloat_down") is not None and max(up.data.get("bloat_down") or 0, up.data.get("bloat_up") or 0) > 100:
        out.append(("warn", "Bufferbloat: your latency balloons when the line is busy. Enable SQM/QoS on the router, or pause "
                            "downloads/streams while gaming."))
    w = g("web")
    if w and w.status == FAIL and tcp != FAIL:
        out.append(("bad", "Websites fail to load even though connections open – check proxy/VPN/antivirus/DNS filtering."))
    elif w and w.status == WARN:
        out.append(("warn", "Some websites didn't load fully – see details (could be those sites, not you)."))
    v = g("video")
    if v and v.status in (WARN, FAIL):
        out.append(("warn", "Video playback would stutter/buffer: " + v.summary + "."))
    c = g("captive")
    if c and c.status == WARN and "intercept" in c.summary:
        out.append(("warn", "A login page or filter seems to be intercepting traffic (captive portal)."))
    io, px, ex, dr, lk = g("ioc"), g("plex"), g("exposure"), g("drops"), g("link")
    if dr and dr.status == FAIL and lk and lk.status == FAIL:
        out.insert(0, ("bad", "Your connection keeps dropping and recovering while the upload link is saturated or lopsided. A busy upload "
                              "starves the router's keep-alives, so the ISP resets the session. Usually the cause is something ordinary "
                              "(cloud backup, torrent, remote streaming, someone uploading video). Check the 'Drops & link' tab: if this PC "
                              "sends little, another device is the sender. Less commonly a device is being misused – see the possible "
                              "causes below."))
    elif dr and dr.status == FAIL:
        out.append(("bad", "Your connection repeatedly drops and recovers in a cycle (" + dr.summary + "). See the possible causes below; "
                           "comparing with upload usage often finds the reason."))
    elif lk and lk.status == FAIL:
        out.append(("warn", "Your upload link is saturated or lopsided (" + lk.summary + "). Find what is sending the data."))
    if px and px.status == FAIL:
        out.insert(0, ("warn", "A Plex server on your network is an older version with known security flaws (Plex2Shell). Updating to "
                               "1.43.3.10861 or newer is recommended. If port 32400 is open to the internet it is worth a quick check – "
                               "tools/plex_server_check.sh does this on the server."))
    if ex and ex.status == WARN and ex.data.get("plex_open"):
        out.append(("warn", "Your router forwards Plex (32400) to the internet. That's fine if the server is up to date; remove the "
                            "forward if you don't need remote access."))
    if io and io.status == FAIL:
        out.insert(0, ("warn", "This PC has a connection to an address on the Plex2Shell attacker list (" + io.details[-1] + "). It can be a "
                               "coincidence or a stale list entry, but a malware scan is a sensible next step."))
    t = g("targets")
    if t and t.status != PASS and t.status != SKIP:
        out.append(("warn", "Your monitored targets show problems – open their tabs to see exactly which hop loses traffic."))
    if not out:
        out.append(("ok", "No problems found. Everything tested works. If trouble is intermittent, leave monitoring running "
                          "– diagnostics will re-run automatically when a target stops answering."))
    return out


# --------------------------------------------------------------------------- numbers for trend graphs
METRIC_INFO = {   # key -> (label, unit)
    "router_ms": ("Router ping", "ms"), "internet_ms": ("Internet ping", "ms"), "router_loss": ("Router loss", "%"),
    "internet_loss": ("Internet loss", "%"), "dns_ms": ("DNS lookup", "ms"), "web_ms": ("Website load (avg)", "ms"),
    "web_ok_pct": ("Websites loaded", "%"), "tcp_ok": ("TCP connections OK", "of 6"),
    "udp_loss": ("UDP loss (game traffic)", "%"), "udp_ports_ok": ("UDP source ports OK", "of 12"),
    "nat_strict": ("Strict NAT (1=yes)", ""), "echo_up_loss": ("Upload loss (echo server)", "%"),
    "echo_down_loss": ("Download loss (echo server)", "%"), "speed_down": ("Download", "Mbps"),
    "speed_up": ("Upload", "Mbps"), "bloat_down": ("Extra ping under download", "ms"),
    "bloat_up": ("Extra ping under upload", "ms"), "video_startup_s": ("Video start time", "s"),
    "drops_1h": ("Connection drops in last hour", ""), "link_up_mbps": ("Link upload", "Mbps"),
    "link_down_mbps": ("Link download", "Mbps"), "video_stalls": ("Video stalls", ""), "video_stall_s": ("Video frozen time", "s"), "video_mbps": ("Video throughput", "Mbps"),
}


def metrics(results: dict) -> dict[str, float]:
    m: dict[str, float] = {}

    def put(k, v):
        if v is not None:
            m[k] = float(v)
    d = lambda key: (results[key].data if key in results and results[key].status != SKIP else {}) or {}
    g, i, n, t, u, e, w, sp, v = (d(k) for k in ("gateway", "internet", "dns", "tcp", "udp", "echo", "web", "speed", "video"))
    put("router_ms", g.get("avg")); put("router_loss", g.get("loss"))
    put("internet_ms", i.get("avg")); put("internet_loss", i.get("loss"))
    put("dns_ms", n.get("avg")); put("tcp_ok", t.get("ok"))
    put("udp_loss", u.get("loss")); put("udp_ports_ok", u.get("ports_ok"))
    if u.get("nat") in ("open", "strict"):
        put("nat_strict", 1 if u["nat"] == "strict" else 0)
    put("echo_up_loss", e.get("up_loss")); put("echo_down_loss", e.get("down_loss"))
    if w.get("n"):
        put("web_ok_pct", 100.0 * w["good"] / w["n"])
    put("web_ms", w.get("avg"))
    put("speed_down", sp.get("down")); put("speed_up", sp.get("up"))
    put("bloat_down", sp.get("bloat_down")); put("bloat_up", sp.get("bloat_up"))
    lk, dr = d("link"), d("drops")
    put("link_up_mbps", lk.get("up")); put("link_down_mbps", lk.get("down")); put("drops_1h", dr.get("drops_1h"))
    if v.get("loaded"):
        put("video_startup_s", v.get("startup")); put("video_stalls", v.get("stalls"))
        put("video_stall_s", v.get("stall_time")); put("video_mbps", v.get("mbps"))
    return m


def statuses(results: dict) -> dict[str, tuple[str, str]]:
    return {k: (r.status, r.summary) for k, r in results.items() if r.status != SKIP}


# --------------------------------------------------------------------------- "what else could it be"
def causes(results: dict, limit: int = 6) -> list[dict]:
    """Ranked, calm list of things that could be contributing. Ordinary causes first; security only with evidence."""
    g = results.get
    st = lambda k: g(k).status if g(k) else SKIP
    data = lambda k: (g(k).data if g(k) else None) or {}
    out: list[dict] = []

    def add(w, title, why, fix):
        out.append(dict(weight=w, title=title, why=why, fix=fix))
    drops, link = st("drops") in (WARN, FAIL), st("link") in (WARN, FAIL)
    if link or drops:
        add(3 if link else 1.5, "Something is using a lot of upload",
            "Cloud backup or photo sync (OneDrive, Google Photos, iCloud), torrents, game or Windows updates sharing, a Plex/Jellyfin "
            "remote stream, or someone uploading or live-streaming video can fill the upload and make everything else lag or drop.",
            "Pause backups and torrents, check Task Manager → Performance → Ethernet/Wi‑Fi, and look at per-device usage in your router.")
    if drops:
        add(2.5, "Router or modem needs a restart (or is overheating)",
            "Routers that run for months can get unstable, overheat, or run out of memory, which looks like regular short drops.",
            "Power-cycle the modem and router (30 s off), give them airflow, and install the latest router firmware.")
        add(2, "A line or ISP-side fault",
            "Noise on the line, a loose or damaged connector, or an ISP issue shows up as repeated session resets.",
            "Check the router's log for 'PPPoE' / 'link down' entries, re-seat the cables, and ask your ISP to test the line.")
    gw = g("gateway")
    if gw and gw.status in (WARN, FAIL) and gw.data:
        add(2.5, "Wi‑Fi interference or a weak/loose local connection",
            "Packet loss or high ping to your own router means the problem is between this PC and the router.",
            "Try a network cable, move closer to the router, change the Wi‑Fi channel, and check the cable ends.")
    sp = data("speed")
    if max(sp.get("bloat_down") or 0, sp.get("bloat_up") or 0) > 100:
        add(2.5, "Bufferbloat",
            "Latency balloons when the line is busy, so games and calls lag whenever anyone downloads or uploads.",
            "Enable SQM / Smart Queue / QoS on the router (set it to ~90–95% of your real speed), or pause big transfers.")
    if st("udp") in (WARN, FAIL):
        add(2, "Router, firewall or NAT handling UDP badly",
            "Games and voice chat use UDP. Strict NAT, blocked ports or an over-eager firewall/antivirus can break one direction.",
            "Enable UPnP on the router, check Windows Firewall / antivirus rules for the game, or try the router's DMZ/port forwarding.")
    if st("dns") in (WARN, FAIL):
        add(2, "DNS (name lookup) problems", "Slow or failing name lookups make sites hang even though the connection is fine.",
            "Set DNS to 1.1.1.1 / 8.8.8.8 in your adapter or router.")
    if st("web") in (WARN, FAIL) and st("tcp") != FAIL:
        add(1.5, "VPN, proxy, antivirus web-shield or DNS filter",
            "Software sitting in the middle can block or slow some sites while pings look perfect.",
            "Temporarily turn off the VPN / web-shield and test again.")
    if st("video") in (WARN, FAIL) and st("speed") == PASS:
        add(1.5, "Peak-time congestion or the streaming service itself",
            "Speed is fine but video stutters – often evening congestion in the ISP network or the far end.",
            "Compare at a different time of day and with another service.")
    if g("drops") and "cluster" in " ".join(g("drops").details):
        add(1.5, "A scheduled task or evening congestion",
            "Drops at the same time on several days usually mean something runs on a schedule (backups, updates, a household routine) "
            "or your area is busiest then.", "Note what runs at those hours; compare with the time-of-day chart in History.")
    if drops or link:
        add(0.8, "A device being misused (less common, cheap to rule out)",
            "Devices reachable from the internet that are out of date – a Plex server, NAS, camera or old router – can occasionally be "
            "hijacked to send traffic. It is rarely the cause, but easy to rule out.",
            "Update those devices, remove unneeded port forwards (32400, 22, 3389…), and look at which device is sending data in your router.")
    if st("plex") == FAIL or st("ioc") == FAIL or data("exposure").get("plex_open"):
        add(2.8, "Security: an out-of-date exposed service or a listed bad address",
            "The checks found an old Plex version, a Plex forward to the internet, or a connection to an address on a known-bad list.",
            "Update Plex to 1.43.3.10861+, close port 32400 until updated, run tools/plex_server_check.sh on the server, and run a malware scan.")
    out.sort(key=lambda d: -d["weight"])
    return out[:limit]
