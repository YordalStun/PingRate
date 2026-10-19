import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pingrate.history import History


def test_history_roundtrip(tmp_path):
    h = History(tmp_path / "h.db", autoflush=False)
    now = time.time() - 600
    for i in range(120):
        h.add("a.com", 0, 10.0 if i < 60 else None, "1.2.3.4", now + i)
        h.add("a.com", 3, 5.0, "10.0.0.3", now + i)
    h.flush()
    h.add("a.com", 0, 20.0, None, now)       # merges into an existing minute (upsert)
    s = h.summary("a.com", now - 100)
    assert s["sent"] == 121 and s["recv"] == 61 and s["max"] == 20.0
    assert h.targets() == ["a.com"]
    hops = h.hop_loss("a.com", now - 100)
    assert hops[0]["ttl"] == 3 and hops[0]["loss"] == 0 and hops[-1]["ttl"] == 0 and hops[-1]["loss"] > 40
    assert len(h.outages("a.com", now - 100)) == 1
    assert h.series("a.com", now - 100, 60)
    h.save_diag("manual", [{"name": "x"}], [["ok", "fine"]], "report text")
    assert h.diag_runs()[0]["report"] == "report text"
