"""Low-level network probes used by the diagnostics tests (stdlib only)."""
from __future__ import annotations

import os
import select
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlparse

from .. import icmp

UA = "Mozilla/5.0 PingRate/1.0"


# ------------------------------------------------------------------ DNS over UDP
def dns_query(server: str, name: str, timeout: float = 2.0) -> float | None:
    """Return ms for a UDP DNS A-query to `server`, or None on failure."""
    qid = os.urandom(2)
    q = qid + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    q += b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00\x00\x01\x00\x01"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        t0 = time.perf_counter()
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(1024)
        ms = (time.perf_counter() - t0) * 1000
        if data[:2] == qid and (data[3] & 0x0F) == 0 and struct.unpack(">H", data[6:8])[0] > 0:
            return ms
    except OSError:
        pass
    finally:
        s.close()
    return None


def system_dns(name: str) -> float | None:
    t0 = time.perf_counter()
    try:
        socket.getaddrinfo(name, 443, socket.AF_INET)
        return (time.perf_counter() - t0) * 1000
    except OSError:
        return None


# ------------------------------------------------------------------ TCP
def tcp_connect(host: str, port: int, timeout: float = 3.0) -> float | None:
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout):
            return (time.perf_counter() - t0) * 1000
    except OSError:
        return None


# ------------------------------------------------------------------ STUN (UDP both ways)
_COOKIE = 0x2112A442


def _stun_req() -> tuple[bytes, bytes]:
    tid = os.urandom(12)
    return struct.pack(">HHI", 1, 0, _COOKIE) + tid, tid


def _stun_parse(data: bytes):
    if len(data) < 20 or data[4:8] != struct.pack(">I", _COOKIE):
        return None, None
    tid, pos, mapped = data[8:20], 20, None
    while pos + 4 <= len(data):
        t, ln = struct.unpack(">HH", data[pos:pos + 4])
        v = data[pos + 4:pos + 4 + ln]
        if t == 0x0020 and ln >= 8:
            mapped = (socket.inet_ntoa(struct.pack(">I", struct.unpack(">I", v[4:8])[0] ^ _COOKIE)),
                      struct.unpack(">H", v[2:4])[0] ^ (_COOKIE >> 16))
        elif t == 0x0001 and ln >= 8 and mapped is None:
            mapped = (socket.inet_ntoa(v[4:8]), struct.unpack(">H", v[2:4])[0])
        pos += 4 + ((ln + 3) & ~3)
    return tid, mapped


def stun_resolve(host: str, port: int):
    try:
        return socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    except OSError:
        return None


def stun_burst(server, count=30, interval=0.04, sock=None, wait=1.5) -> dict:
    """Send `count` binding requests from ONE socket; every reply proves the
    packet made it out AND back."""
    own = sock is None
    s = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    pending: dict[bytes, float] = {}
    rtts: list[float] = []
    mapped = set()

    def drain(deadline):
        while True:
            left = deadline - time.perf_counter()
            if left <= 0 or not select.select([s], [], [], min(left, 0.05))[0]:
                if left <= 0:
                    return
                continue
            try:
                data, _ = s.recvfrom(2048)
            except OSError:
                return
            tid, m = _stun_parse(data)
            if tid in pending:
                rtts.append((time.perf_counter() - pending.pop(tid)) * 1000)
                if m:
                    mapped.add(m)

    try:
        for _ in range(count):
            pkt, tid = _stun_req()
            pending[tid] = time.perf_counter()
            try:
                s.sendto(pkt, server)
            except OSError:
                break
            drain(time.perf_counter() + interval)
        drain(time.perf_counter() + wait)
    finally:
        if own:
            s.close()
    return {"sent": count, "recv": len(rtts), "rtts": rtts, "mapped": mapped}


# ------------------------------------------------------------------ Echo server (optional)
MAGIC = b"PRv1"


def echo_test(host: str, port: int, count=100, interval=0.02) -> dict | None:
    """Per-direction loss against tools/echo_server.py."""
    try:
        addr = (socket.gethostbyname(host), port)
    except OSError:
        return None
    sess = os.urandom(8)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    got: set[int] = set()
    server_count = 0
    summary = None

    def drain(t):
        nonlocal server_count, summary
        end = time.perf_counter() + t
        while time.perf_counter() < end:
            if select.select([s], [], [], 0.02)[0]:
                d, _ = s.recvfrom(512)
                if len(d) >= 21 and d[:4] == MAGIC and d[4:12] == sess:
                    typ, seq, cnt = d[12], *struct.unpack(">II", d[13:21])
                    server_count = max(server_count, cnt)
                    if typ == 1:
                        summary = cnt
                    else:
                        got.add(seq)

    try:
        for i in range(count):
            s.sendto(MAGIC + sess + b"\x00" + struct.pack(">I", i), addr)
            drain(interval)
        drain(1.0)
        for _ in range(8):
            if summary is not None:
                break
            s.sendto(MAGIC + sess + b"\x01" + struct.pack(">I", 0), addr)
            drain(0.5)
    finally:
        s.close()
    if summary is None and not got:
        return {"sent": count, "server_recv": None, "client_recv": 0}
    srv = summary if summary is not None else server_count
    return {"sent": count, "server_recv": srv, "client_recv": len(got)}


# ------------------------------------------------------------------ HTTP(S) with timing
def http_probe(url: str, timeout: float = 8.0, max_body: int = 400_000) -> dict:
    out = {"url": url, "ok": False}
    for _ in range(5):
        u = urlparse(url)
        host, https = u.hostname, u.scheme == "https"
        port = u.port or (443 if https else 80)
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        try:
            t0 = time.perf_counter()
            ai = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4]
            t_dns = time.perf_counter()
            sock = socket.create_connection(ai, timeout)
            t_tcp = time.perf_counter()
            if https:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            t_tls = time.perf_counter()
            sock.settimeout(timeout)
            sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: {UA}\r\n"
                         "Accept: */*\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n".encode())
            buf = b""
            t_first = None
            while len(buf) < max_body + 20000:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                if t_first is None:
                    t_first = time.perf_counter()
                buf += chunk
            sock.close()
        except (OSError, ssl.SSLError) as e:
            out["error"] = getattr(e, "strerror", None) or str(e) or type(e).__name__
            return out
        t_end = time.perf_counter()
        head, _, body = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin1").split("\r\n")
        try:
            status = int(lines[0].split()[1])
        except (IndexError, ValueError):
            out["error"] = "Invalid HTTP response"
            return out
        hdr = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
        if status in (301, 302, 303, 307, 308) and "location" in hdr:
            loc = hdr["location"]
            url = loc if loc.startswith("http") else f"{u.scheme}://{u.netloc}{loc}"
            continue
        out.update(ok=status < 400 and len(body) > 200, status=status, bytes=len(body), url=url,
                   dns=(t_dns - t0) * 1000, connect=(t_tcp - t_dns) * 1000,
                   tls=(t_tls - t_tcp) * 1000 if https else 0.0,
                   ttfb=((t_first or t_end) - t_tls) * 1000, total=(t_end - t0) * 1000)
        if status < 400 and len(body) <= 200:
            out["error"] = "Page came back empty"
        return out
    out["error"] = "Too many redirects"
    return out


# ------------------------------------------------------------------ Speed test (Cloudflare)
CF = "speed.cloudflare.com"


def _worker_down(stop, counter, lock, errs):
    while not stop.is_set():
        try:
            ctx = ssl.create_default_context()
            s = ctx.wrap_socket(socket.create_connection((CF, 443), 6), server_hostname=CF)
            s.sendall(f"GET /__down?bytes=50000000 HTTP/1.1\r\nHost: {CF}\r\nUser-Agent: {UA}\r\n"
                      "Connection: close\r\n\r\n".encode())
            s.settimeout(6)
            first = True
            while not stop.is_set():
                d = s.recv(131072)
                if not d:
                    break
                with lock:
                    counter[0] += len(d)
            s.close()
        except OSError as e:
            errs.append(str(e))
            time.sleep(0.3)


def _worker_up(stop, counter, lock, errs):
    chunk = os.urandom(65536)
    while not stop.is_set():
        try:
            ctx = ssl.create_default_context()
            s = ctx.wrap_socket(socket.create_connection((CF, 443), 6), server_hostname=CF)
            size = 20_000_000
            s.sendall(f"POST /__up HTTP/1.1\r\nHost: {CF}\r\nUser-Agent: {UA}\r\nContent-Length: {size}\r\n"
                      "Content-Type: application/octet-stream\r\nConnection: close\r\n\r\n".encode())
            s.settimeout(6)
            sent = 0
            while sent < size and not stop.is_set():
                s.sendall(chunk)
                sent += len(chunk)
                with lock:
                    counter[0] += len(chunk)
            s.close()
        except OSError as e:
            errs.append(str(e))
            time.sleep(0.3)


def speed_phase(direction: str, seconds=8.0, threads=4, progress=None, latency_host="1.1.1.1") -> dict:
    """Run a download or upload phase. Also samples ICMP latency under load (bufferbloat)."""
    stop, lock, errs = threading.Event(), threading.Lock(), []
    counter = [0]
    fn = _worker_down if direction == "down" else _worker_up
    ts = [threading.Thread(target=fn, args=(stop, counter, lock, errs), daemon=True) for _ in range(threads)]
    for t in ts:
        t.start()
    lat: list[float] = []
    t0 = time.perf_counter()
    warm_bytes, warm_t = None, None
    while time.perf_counter() - t0 < seconds:
        r = icmp.ping(latency_host, 128, 1000)
        if r.kind == icmp.OK:
            lat.append(r.rtt)
        time.sleep(0.15)
        el = time.perf_counter() - t0
        if warm_bytes is None and el >= 1.5:                  # ignore TCP ramp-up
            warm_bytes, warm_t = counter[0], el
        if progress and el > 0.5:
            progress(f"{direction}load: {counter[0] * 8 / el / 1e6:.0f} Mbps")
    stop.set()
    el = time.perf_counter() - t0
    if warm_bytes is not None and el > warm_t:
        mbps = (counter[0] - warm_bytes) * 8 / (el - warm_t) / 1e6
    else:
        mbps = counter[0] * 8 / el / 1e6
    return {"mbps": mbps if counter[0] else 0.0, "bytes": counter[0], "latency": lat, "errors": errs[:3]}


# ------------------------------------------------------------------ Video streaming simulation
VIDEO_URLS = [
    "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4",
    "https://download.blender.org/peach/bigbuckbunny_movies/big_buck_bunny_480p_h264.mov",
    "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_30MB.mp4",
]


def video_test(url: str, seconds=15.0, bitrate_mbps=5.0, progress=None) -> dict:
    """Download a real video and model a player with a buffer playing at `bitrate_mbps`."""
    u = urlparse(url)
    res = {"url": url, "loaded": False, "startup": None, "stalls": 0, "stall_time": 0.0,
           "mbps": 0.0, "gaps": 0}
    t0 = time.perf_counter()
    try:
        s = socket.create_connection((u.hostname, u.port or (443 if u.scheme == "https" else 80)), 6)
        if u.scheme == "https":
            s = ssl.create_default_context().wrap_socket(s, server_hostname=u.hostname)
        s.sendall(f"GET {u.path or '/'}{'?' + u.query if u.query else ''} HTTP/1.1\r\nHost: {u.hostname}\r\n"
                  f"User-Agent: {UA}\r\nConnection: close\r\n\r\n".encode())
        s.settimeout(5)
        head = b""
        while b"\r\n\r\n" not in head:
            d = s.recv(65536)
            if not d:
                raise OSError("connection closed")
            head += d
        head, _, body = head.partition(b"\r\n\r\n")
        status = int(head.split(b" ", 2)[1])
        if status != 200:
            res["error"] = f"Server answered HTTP {status}"
            return res
    except (OSError, ValueError) as e:
        res["error"] = getattr(e, "strerror", None) or str(e)
        return res

    rate = bitrate_mbps * 1e6 / 8                    # bytes/s consumed by playback
    buffered = float(len(body))                      # bytes in the buffer
    total = len(body)
    playing, stalled_at = False, None
    last_t = last_rx = time.perf_counter()
    start_need = rate * 2.0                          # 2 s of video before starting / resuming
    s.settimeout(1.0)
    while time.perf_counter() - t0 < seconds:
        try:
            d = s.recv(131072)
            if not d:
                break
        except socket.timeout:
            d = b""
        except OSError:
            break
        now = time.perf_counter()
        if d:
            if now - last_rx > 0.5 and res["loaded"]:
                res["gaps"] += 1
            last_rx = now
        dt, last_t = now - last_t, now
        if playing:
            buffered -= rate * dt
            if buffered <= 0:
                buffered, playing, stalled_at = 0.0, False, now
                res["stalls"] += 1
        buffered += len(d)
        total += len(d)
        if not playing and buffered >= start_need:
            if not res["loaded"]:
                res["loaded"], res["startup"] = True, now - t0
            elif stalled_at:
                res["stall_time"] += now - stalled_at
            playing, stalled_at = True, None
        if progress:
            progress(f"video: {total * 8 / (now - t0) / 1e6:.1f} Mbps, {res['stalls']} stalls")
    el = time.perf_counter() - t0
    if not playing and stalled_at:
        res["stall_time"] += time.perf_counter() - stalled_at
    res["mbps"] = total * 8 / el / 1e6
    s.close()
    if total == 0:
        res["error"] = "No data received"
    return res
