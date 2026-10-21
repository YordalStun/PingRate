"""Minimal UPnP-IGD client: router byte counters, link capacity and port forwards. No credentials needed."""
from __future__ import annotations

import re
import socket
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urljoin

SSDP = ("239.255.255.250", 1900)
_OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # never go through a proxy for the LAN


def discover(timeout: float = 2.5) -> list[str]:
    """Return LOCATION urls of Internet Gateway Devices answering SSDP."""
    found: list[str] = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.settimeout(0.5)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        for st in ("urn:schemas-upnp-org:device:InternetGatewayDevice:1", "urn:schemas-upnp-org:device:InternetGatewayDevice:2"):
            msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
                   f"ST: {st}\r\n\r\n").encode()
            try:
                s.sendto(msg, SSDP)
            except OSError:
                return found
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, _ = s.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m and m.group(1).decode() not in found:
                found.append(m.group(1).decode())
    finally:
        s.close()
    return found


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_services(xml_text: str, base: str) -> dict[str, tuple[str, str]]:
    """service type -> (full control url, service type string)."""
    root = ET.fromstring(xml_text)
    url_base = next((e.text for e in root.iter() if _local(e.tag) == "URLBase" and e.text), base)
    out = {}
    for svc in (e for e in root.iter() if _local(e.tag) == "service"):
        f = {_local(c.tag): (c.text or "").strip() for c in svc}
        if f.get("serviceType") and f.get("controlURL"):
            out[f["serviceType"].split(":")[-2]] = (urljoin(url_base, f["controlURL"]), f["serviceType"])
    return out


def parse_soap(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in re.finditer(r"<(New\w+)>([^<]*)</\1>", text)}


def soap(control: str, stype: str, action: str, args: dict | None = None, timeout: float = 3.0) -> dict[str, str]:
    body = "".join(f"<{k}>{v}</{k}>" for k, v in (args or {}).items())
    env = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
           's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
           f'<u:{action} xmlns:u="{stype}">{body}</u:{action}></s:Body></s:Envelope>').encode()
    req = urllib.request.Request(control, env, {"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{stype}#{action}"'})
    with _OPEN.open(req, timeout=timeout) as r:
        return parse_soap(r.read().decode("utf-8", "replace"))


class UpnpRouter:
    kind = "UPnP"
    wrap = 2 ** 32

    def __init__(self, location: str):
        self.location = location
        with _OPEN.open(location, timeout=3) as r:
            self.services = parse_services(r.read().decode("utf-8", "replace"), location)
        if "WANCommonInterfaceConfig" not in self.services:
            raise OSError("router has no WANCommonInterfaceConfig service")
        self.name = f"UPnP router {re.sub(r'^https?://', '', location).split('/')[0]}"

    def _call(self, svc: str, action: str, args=None):
        ctl, st = self.services[svc]
        return soap(ctl, st, action, args)

    def counters(self) -> tuple[int, int]:
        """(bytes sent upstream, bytes received) since router boot."""
        sent = self._call("WANCommonInterfaceConfig", "GetTotalBytesSent")
        recv = self._call("WANCommonInterfaceConfig", "GetTotalBytesReceived")
        return int(sent["NewTotalBytesSent"]), int(recv["NewTotalBytesReceived"])

    def capacity(self) -> tuple[float | None, float | None]:
        """(upstream, downstream) link rate in bits/s as reported by the modem, if known."""
        try:
            r = self._call("WANCommonInterfaceConfig", "GetCommonLinkProperties")
            up, dn = int(r.get("NewLayer1UpstreamMaxBitRate", 0)), int(r.get("NewLayer1DownstreamMaxBitRate", 0))
            return (up or None), (dn or None)
        except (OSError, ValueError, KeyError):
            return None, None

    def _wan(self):
        for k in ("WANIPConnection", "WANPPPConnection"):
            if k in self.services:
                return k
        return None

    def external_ip(self) -> str | None:
        k = self._wan()
        try:
            return self._call(k, "GetExternalIPAddress").get("NewExternalIPAddress") if k else None
        except OSError:
            return None

    def port_mappings(self, limit: int = 128) -> list[dict]:
        k = self._wan()
        out = []
        if not k:
            return out
        for i in range(limit):
            try:
                r = self._call(k, "GetGenericPortMappingEntry", {"NewPortMappingIndex": i})
            except OSError:
                break
            if "NewExternalPort" not in r:
                break
            out.append(dict(ext_port=int(r.get("NewExternalPort", 0)), proto=r.get("NewProtocol", ""),
                            client=r.get("NewInternalClient", ""), int_port=int(r.get("NewInternalPort", 0) or 0),
                            desc=r.get("NewPortMappingDescription", ""), enabled=r.get("NewEnabled", "1") == "1"))
        return out


def find_router() -> UpnpRouter | None:
    for loc in discover():
        try:
            return UpnpRouter(loc)
        except (OSError, ET.ParseError):
            continue
    return None
