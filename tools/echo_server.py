#!/usr/bin/env python3
"""PingRate echo server – run this on a machine OUTSIDE your home network (VPS / friend's PC).

    python echo_server.py [port]        (default UDP port 40404; open it in the firewall)

It replies to every probe and tells the client how many probes it has received, which lets
PingRate separate upstream loss (you -> server) from downstream loss (server -> you).
"""
import socket
import struct
import sys

MAGIC = b"PRv1"
port = int(sys.argv[1]) if len(sys.argv) > 1 else 40404
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", port))
sessions: dict[bytes, set] = {}
print(f"PingRate echo server listening on UDP {port}")
while True:
    data, addr = sock.recvfrom(512)
    if len(data) < 17 or data[:4] != MAGIC:
        continue
    sess, typ, seq = data[4:12], data[12], struct.unpack(">I", data[13:17])[0]
    seen = sessions.setdefault(sess, set())
    if len(sessions) > 5000:
        sessions.pop(next(iter(sessions)))
    if typ == 0:
        seen.add(seq)
    sock.sendto(MAGIC + sess + bytes([typ]) + struct.pack(">II", seq, len(seen)), addr)
