import os, socket, sys, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pingrate import ioc, plex, snmp, upnp

IGD = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><URLBase>http://192.168.1.1:5000/</URLBase><device>
<serviceList><service><serviceType>urn:schemas-upnp-org:service:WANCommonInterfaceConfig:1</serviceType>
<controlURL>/ctl/CmnIfCfg</controlURL></service></serviceList>
<deviceList><device><deviceList><device><serviceList><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
<controlURL>/ctl/IPConn</controlURL></service></serviceList></device></deviceList></device></deviceList></device></root>"""


def test_upnp_parsing():
    s = upnp.parse_services(IGD, "http://x/")
    assert s["WANCommonInterfaceConfig"][0] == "http://192.168.1.1:5000/ctl/CmnIfCfg"
    assert s["WANIPConnection"][1].endswith("WANIPConnection:1")
    assert upnp.parse_soap("<u:R><NewTotalBytesSent>123</NewTotalBytesSent></u:R>") == {"NewTotalBytesSent": "123"}


def test_snmp_roundtrip():
    # fake agent answering any GET with Counter64 = 5_000_000_000
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); srv.bind(("127.0.0.1", 0)); port = srv.getsockname()[1]

    def agent():
        data, addr = srv.recvfrom(2048)
        val = snmp._tlv(0x46, (5_000_000_000).to_bytes(5, "big"))
        req_id = 1                                   # the client doesn't verify it
        oid = snmp._oid("1.3.6.1.2.1.31.1.1.1.10.2")
        vb = snmp._tlv(0x30, snmp._tlv(0x30, oid + val))
        pdu = snmp._tlv(0xA2, snmp._int(req_id) + snmp._int(0) + snmp._int(0) + vb)
        srv.sendto(snmp._tlv(0x30, snmp._int(1) + snmp._tlv(0x04, b"public") + pdu), addr)
    threading.Thread(target=agent, daemon=True).start()
    assert snmp.get("127.0.0.1", "public", "1.3.6.1.2.1.31.1.1.1.10.2", port) == 5_000_000_000


def test_plex_versions():
    assert plex.is_vulnerable("1.43.3.10828-abc") is True
    assert plex.is_vulnerable("1.43.3.10861-abc") is False
    assert plex.is_vulnerable("1.44.0.1000") is False
    assert plex.is_vulnerable("garbage") is None


def test_ioc_netstat():
    out = "  TCP    192.168.1.5:50123    94.154.43.88:443    ESTABLISHED\n  TCP    192.168.1.5:50124    1.1.1.1:443    ESTABLISHED\n"
    assert ioc.parse_netstat(out) == {"94.154.43.88", "1.1.1.1"}
