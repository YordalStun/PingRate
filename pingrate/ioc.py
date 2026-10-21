"""Indicators of compromise (Plex2Shell brief) + checks this PC can do for them."""
from __future__ import annotations

import json
import re
import subprocess
import sys

BUILTIN = {
    "ips": {
        "94.154.43.88": "Plex2Shell – initial exploit requests",
        "91.92.40.202": "Plex2Shell – payload delivery",
        "45.194.92.222": "Plex2Shell – scanning / exploit",
    },
    "domains": {
        "91-92-40-202.sslip.io": "Plex2Shell payload delivery",
        "ota.proxystream.st": "XMRig miner payload host",
        "global.relay.proxystream.st": "AISURU-linked relay",
    },
    "sha256": {
        "a654ffc9ea1d3af1cb07feeefe8ee078a0faaaf60b31aa20cfbe082d6c22696c": "kworker DDoS bot",
        "91e8bb8e9a2e76967a456dec1467fe55b9cde78ece6c293e5ded94ea5a80bf77": "attacker's curl binary",
    },
}


def load() -> dict:
    """Built-in list merged with %APPDATA%\\PingRate\\iocs.json ({"ips": {...}, "domains": {...}}) if present."""
    out = {k: dict(v) for k, v in BUILTIN.items()}
    try:
        from .settings import data_dir
        extra = json.loads((data_dir() / "iocs.json").read_text(encoding="utf-8"))
        for k in out:
            v = extra.get(k, {})
            out[k].update({x: "user-added" for x in v} if isinstance(v, list) else v)
    except Exception:
        pass
    return out


def _run(cmd) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15, errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return ""


def parse_netstat(text: str) -> set[str]:
    """Remote IPv4 addresses from `netstat -n` output."""
    return {m.group(3) for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+):(\d+)\s+(\d+\.\d+\.\d+\.\d+):(\d+)", text)}


def check_connections(iocs: dict | None = None) -> list[tuple[str, str]]:
    iocs = iocs or load()
    remote = parse_netstat(_run(["netstat", "-n"]))
    return [(ip, why) for ip, why in iocs["ips"].items() if ip in remote]


def check_dns_cache(iocs: dict | None = None) -> list[tuple[str, str]]:
    if sys.platform != "win32":
        return []
    iocs = iocs or load()
    text = _run(["ipconfig", "/displaydns"]).lower()
    return [(d, why) for d, why in iocs["domains"].items() if d.lower() in text]
