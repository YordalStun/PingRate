import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pingrate.incidents import LinkSample, analyze_drops, analyze_link


def test_cyclic_drops():
    now = time.time()
    ev = [(now - 3000 + i * 600, now - 3000 + i * 600 + 60) for i in range(5)]
    a = analyze_drops(ev, now, None, "1.1.1.1")
    assert a and a[0].level == "bad" and "regular cycle" in a[0].detail and "5 in the last hour" in a[0].title


def test_three_drops_is_normal():
    now = time.time()
    assert not analyze_drops([(now - 600 * i - 100, now - 600 * i - 50) for i in range(3)], now)


def test_ongoing_counts():
    now = time.time()
    ev = [(now - 900 * i - 200, now - 900 * i - 100) for i in range(3)]
    assert analyze_drops(ev, now, now - 10)


def test_time_of_day_cluster():
    now = time.time()
    ev = [(now - d * 86400 - 5, now - d * 86400) for d in range(1, 5)]       # same time on 4 days
    assert any(a.key.startswith("tod") for a in analyze_drops(ev, now))


def test_saturation_and_asymmetry():
    now = time.time()
    s = [LinkSample(now - 300 + i * 5, 120e6, 1.2e6, 2e6, 1e6) for i in range(61)]
    a = {x.key: x for x in analyze_link(s, 150e6, now)}
    assert "link_saturated" in a and "another device" in a["link_saturated"].detail
    assert "link_asym" in a and "100×" in a["link_asym"].title


def test_quiet_link():
    now = time.time()
    s = [LinkSample(now - 300 + i * 5, 3e6, 20e6, 1e6, 10e6) for i in range(61)]
    assert analyze_link(s, 150e6, now) == []
