"""LAN scan for Plex Media Servers and Plex2Shell (CVE-2026-96652/54/55/56) exposure."""
from __future__ import annotations

import re
import socket
import urllib.request
from concurrent.futures import ThreadPoolExecutor

FIXED = (1, 43, 3, 10861)
PORT = 32400
_OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def parse_version(v: str) -> tuple[int, ...] | None:
    m = re.match(r"(\d+)\.(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def is_vulnerable(version: str) -> bool | None:
    t = parse_version(version)
    return None if t is None else t < FIXED


def local_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))          # no packet is sent; just picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def identity(host: str, timeout: float = 2.0) -> dict | None:
    """Plex answers GET /identity without authentication."""
    try:
        with _OPEN.open(f"http://{host}:{PORT}/identity", timeout=timeout) as r:
            txt = r.read(20000).decode("utf-8", "replace")
    except OSError:
        return None
    if "MediaContainer" not in txt:
        return None
    ver = re.search(r'version="([^"]+)"', txt)
    mid = re.search(r'machineIdentifier="([^"]+)"', txt)
    version = ver.group(1) if ver else ""
    return dict(host=host, version=version, machine=mid.group(1) if mid else "", vulnerable=is_vulnerable(version))


def _open(host: str) -> str | None:
    try:
        with socket.create_connection((host, PORT), 0.4):
            return host
    except OSError:
        return None


def scan_lan(progress=lambda m: None) -> tuple[list[dict], str | None]:
    ip = local_ip()
    if not ip or ip.startswith("127."):
        return [], ip
    base = ip.rsplit(".", 1)[0]
    progress(f"Scanning {base}.0/24 for Plex servers…")
    with ThreadPoolExecutor(64) as ex:
        hits = [h for h in ex.map(_open, [f"{base}.{i}" for i in range(1, 255)]) if h]
    return [i for i in (identity(h) for h in hits) if i], ip
