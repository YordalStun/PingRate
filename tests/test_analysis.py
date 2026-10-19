import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pingrate.analysis import analyze_route
from pingrate.stats import HopStats


def mk(ttl, pattern, n=20, addr=None):
    h = HopStats(ttl); h.addr = addr or f"10.0.0.{ttl}"
    for i in range(n):
        h.add(pattern(i), None, i)
    return h

good = lambda i: 10.0
dead = lambda i: None


def test_healthy():
    f = analyze_route([mk(1, good), mk(2, good)], mk(3, good))
    assert f.level == "ok"


def test_rate_limited_middle_hop_is_harmless():
    f = analyze_route([mk(1, good), mk(2, dead), mk(3, good)], mk(4, good))
    assert f.level == "ok" and f.hop_status[1][0] == "limited"


def test_loss_origin_found():
    hops = [mk(1, good), mk(2, good), mk(3, dead), mk(4, dead)]
    f = analyze_route(hops, mk(5, dead))
    assert f.level == "bad" and f.origin_row == 2 and "hop 3" in f.title
    assert f.hop_status[3][0] == "down"


def test_destination_only():
    f = analyze_route([mk(1, good), mk(2, good)], mk(3, dead))
    assert f.origin_row == 2 and "Destination" in f.title


def test_partial_loss():
    part = lambda i: None if i % 2 else 10.0
    f = analyze_route([mk(1, good), mk(2, part)], mk(3, part))
    assert f.level == "bad" and f.origin_row == 1


def test_latency_jump():
    f = analyze_route([mk(1, lambda i: 5.0), mk(2, lambda i: 150.0)], mk(3, lambda i: 152.0))
    assert f.level == "warn" and "jumps" in f.title


def test_wait():
    assert analyze_route([], mk(1, good, n=2)).level == "wait"
