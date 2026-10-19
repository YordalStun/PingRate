"""ICMP echo with TTL control and no admin rights.

Windows uses the IcmpSendEcho API (iphlpapi) which also supports TTL, so the same
call powers both normal pings and per-hop route probing. On other platforms the
system ``ping`` is used (development only). ``set_simulation(True)`` swaps in a
fake network so the UI can be demoed offline.
"""
from __future__ import annotations

import hashlib
import random
import re
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

OK, TIMEOUT, TTL_EXPIRED, UNREACHABLE, TOO_BIG, ERROR = (
    "ok", "timeout", "ttl_expired", "unreachable", "too_big", "error")


@dataclass
class Reply:
    kind: str
    addr: str | None = None
    rtt: float | None = None  # milliseconds


_SIM = False
_SIM_T0 = time.time()


def set_simulation(on: bool) -> None:
    global _SIM, _SIM_T0
    _SIM, _SIM_T0 = on, time.time()


def resolve(host: str) -> str:
    """Resolve a hostname / literal to an IPv4 address string (raises OSError)."""
    if _SIM:
        h = int(hashlib.md5(host.encode()).hexdigest(), 16)
        return f"203.0.113.{(h % 250) + 1}"
    return socket.gethostbyname(host)


def ping(ip: str, ttl: int = 128, timeout_ms: int = 1000, size: int = 32, df: bool = False) -> Reply:
    if _SIM:
        return _sim_ping(ip, ttl, timeout_ms)
    return _ping_impl(ip, ttl, timeout_ms, size, df)


# --------------------------------------------------------------------------- Windows
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _IPOpt(ctypes.Structure):
        _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte), ("Flags", ctypes.c_ubyte),
                    ("OptionsSize", ctypes.c_ubyte), ("OptionsData", ctypes.c_void_p)]

    class _EchoReply(ctypes.Structure):
        _fields_ = [("Address", wintypes.DWORD), ("Status", wintypes.DWORD),
                    ("RoundTripTime", wintypes.DWORD), ("DataSize", wintypes.USHORT),
                    ("Reserved", wintypes.USHORT), ("Data", ctypes.c_void_p), ("Options", _IPOpt)]

    _ip = ctypes.WinDLL("iphlpapi", use_last_error=True)
    _ip.IcmpCreateFile.restype = ctypes.c_void_p
    _ip.IcmpCloseHandle.argtypes = [ctypes.c_void_p]
    _ip.IcmpSendEcho.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, wintypes.WORD,
                                 ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    _ip.IcmpSendEcho.restype = wintypes.DWORD
    _local = threading.local()

    def _handle():
        h = getattr(_local, "h", None)
        if not h:
            h = _local.h = _ip.IcmpCreateFile()
        return h

    _STATUS = {0: OK, 11013: TTL_EXPIRED, 11014: TTL_EXPIRED, 11010: TIMEOUT, 11009: TOO_BIG,
               11003: UNREACHABLE, 11004: UNREACHABLE, 11005: UNREACHABLE, 11006: UNREACHABLE,
               11007: UNREACHABLE, 11002: UNREACHABLE}

    def _ping_impl(ip, ttl, timeout_ms, size, df):
        dest = struct.unpack("<L", socket.inet_aton(ip))[0]
        payload = ctypes.create_string_buffer((b"pingrate" * (size // 8 + 1))[:size], size)
        opts = _IPOpt(ttl, 0, 0x02 if df else 0, 0, None)
        buf = ctypes.create_string_buffer(ctypes.sizeof(_EchoReply) + size + 64)
        t0 = time.perf_counter()
        n = _ip.IcmpSendEcho(_handle(), dest, payload, size, ctypes.byref(opts), buf, len(buf), timeout_ms)
        rtt = (time.perf_counter() - t0) * 1000
        if n == 0:
            err = ctypes.get_last_error()
            return Reply(_STATUS.get(err, ERROR))
        r = _EchoReply.from_buffer(buf)
        kind = _STATUS.get(r.Status, ERROR)
        addr = socket.inet_ntoa(struct.pack("<L", r.Address))
        if kind in (OK, TTL_EXPIRED):
            return Reply(kind, addr, max(rtt, 0.05))
        return Reply(kind, addr)

# --------------------------------------------------------------------------- other OS
else:
    def _ping_impl(ip, ttl, timeout_ms, size, df):
        cmd = ["ping", "-n", "-c", "1", "-W", str(max(1, -(-timeout_ms // 1000))), "-t", str(ttl),
               "-s", str(size)] + (["-M", "do"] if df else []) + [ip]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_ms / 1000 + 3).stdout
        except Exception:
            return Reply(ERROR)
        m = re.search(r"time[=<]([\d.]+)", out)
        if m and "bytes from" in out:
            a = re.search(r"from ([\d.]+)", out)
            return Reply(OK, a.group(1) if a else ip, float(m.group(1)))
        if "exceeded" in out:
            a = re.search(r"From ([\d.]+)", out)
            return Reply(TTL_EXPIRED, a.group(1) if a else None, 1.0)
        if "too long" in out or "Frag" in out:
            return Reply(TOO_BIG)
        if "Unreachable" in out:
            return Reply(UNREACHABLE)
        return Reply(TIMEOUT)


# --------------------------------------------------------------------------- simulation
def _sim_ping(ip: str, ttl: int, timeout_ms: int) -> Reply:
    last = int(ip.rsplit(".", 1)[-1])
    broken = last % 2 == 1 and time.time() - _SIM_T0 > 20          # odd targets break after 20s
    dist = 9
    mid = ip.split(".")[2]
    hop_ip = lambda t: ("192.168.1.1" if t == 1 else f"10.{mid}.{t}.1")
    hops = min(ttl, dist)
    rtt = 3 + hops * 3.5 + random.random() * 4 + (35 if hops >= 5 else 0) + (random.random() * 90 if random.random() < 0.02 else 0)
    if broken and ttl >= 6:
        time.sleep(min(timeout_ms, 250) / 1000)
        return Reply(TIMEOUT)
    if not broken and ttl == 4 and random.random() < 0.4:        # router rate-limiting ICMP
        time.sleep(min(timeout_ms, 250) / 1000)
        return Reply(TIMEOUT)
    if random.random() < 0.004:
        time.sleep(min(timeout_ms, 250) / 1000)
        return Reply(TIMEOUT)
    time.sleep(rtt / 1000)
    if ttl >= dist:
        return Reply(OK, ip, rtt)
    return Reply(TTL_EXPIRED, hop_ip(ttl), rtt)
