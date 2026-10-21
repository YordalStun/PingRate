"""Tiny SNMP v2c GET client (enough to read ifHCInOctets / ifHCOutOctets from a router)."""
from __future__ import annotations

import os
import socket


def _len(n: int) -> bytes:
    return bytes([n]) if n < 128 else bytes([0x80 | (n.bit_length() + 7) // 8]) + n.to_bytes((n.bit_length() + 7) // 8, "big")


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _len(len(body)) + body


def _int(v: int) -> bytes:
    return _tlv(0x02, v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big", signed=True))


def _oid(oid: str) -> bytes:
    p = [int(x) for x in oid.strip(".").split(".")]
    body = bytes([40 * p[0] + p[1]])
    for n in p[2:]:
        chunk = [n & 0x7F]
        n >>= 7
        while n:
            chunk.append(0x80 | (n & 0x7F))
            n >>= 7
        body += bytes(reversed(chunk))
    return _tlv(0x06, body)


def build_get(community: str, oid: str, req_id: int) -> bytes:
    vb = _tlv(0x30, _tlv(0x30, _oid(oid) + b"\x05\x00"))
    pdu = _tlv(0xA0, _int(req_id) + _int(0) + _int(0) + vb)
    return _tlv(0x30, _int(1) + _tlv(0x04, community.encode()) + pdu)


def _read(buf: bytes, pos: int):
    tag = buf[pos]
    ln = buf[pos + 1]
    pos += 2
    if ln & 0x80:
        k = ln & 0x7F
        ln = int.from_bytes(buf[pos:pos + k], "big")
        pos += k
    return tag, buf[pos:pos + ln], pos + ln


def parse_response(data: bytes):
    """Return the value of the single varbind (int for counters/gauges/ints, bytes for strings)."""
    _, msg, _ = _read(data, 0)
    pos = 0
    _, _, pos = _read(msg, pos)              # version
    _, _, pos = _read(msg, pos)              # community
    _, pdu, _ = _read(msg, pos)
    pos = 0
    for _ in range(3):                       # request-id, error-status, error-index
        tag, val, pos = _read(pdu, pos)
        if _ == 1 and int.from_bytes(val, "big"):
            raise OSError("SNMP error status")
    _, vbs, _ = _read(pdu, pos)
    _, vb, _ = _read(vbs, 0)
    _, _, pos = _read(vb, 0)                 # oid
    tag, val, _ = _read(vb, pos)
    if tag in (0x02, 0x41, 0x42, 0x43, 0x46):
        return int.from_bytes(val, "big", signed=(tag == 0x02))
    if tag in (0x80, 0x81, 0x82):
        raise OSError("SNMP no such object")
    return val


def get(host: str, community: str, oid: str, port: int = 161, timeout: float = 2.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(build_get(community, oid, int.from_bytes(os.urandom(3), "big")), (host, port))
        return parse_response(s.recvfrom(4096)[0])
    finally:
        s.close()


class SnmpRouter:
    kind = "SNMP"
    wrap = 2 ** 64

    def __init__(self, host: str, community: str, ifindex: int, port: int = 161):
        self.host, self.community, self.ifindex, self.port = host, community, ifindex, port
        try:
            self.name = f"SNMP {host} · " + get(host, community, f"1.3.6.1.2.1.2.2.1.2.{ifindex}", port).decode(errors="replace")
        except OSError as e:
            raise OSError(f"SNMP {host} unreachable: {e}")

    def counters(self) -> tuple[int, int]:
        out = get(self.host, self.community, f"1.3.6.1.2.1.31.1.1.1.10.{self.ifindex}", self.port)
        inn = get(self.host, self.community, f"1.3.6.1.2.1.31.1.1.1.6.{self.ifindex}", self.port)
        return int(out), int(inn)

    def capacity(self):
        try:
            hs = get(self.host, self.community, f"1.3.6.1.2.1.31.1.1.1.15.{self.ifindex}", self.port)   # ifHighSpeed (Mbps)
            return (hs * 1e6 or None), (hs * 1e6 or None)
        except OSError:
            return None, None
