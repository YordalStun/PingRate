import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pingrate.export import build_report
from pingrate.history import History
from pingrate.monitor import TargetMonitor
from pingrate.settings import Settings
from pingrate.stats import HopStats
from pingrate.diag import tests


def test_report(tmp_path):
    m = TargetMonitor("example.com"); m.ip = "1.2.3.4"
    m.hops = [HopStats(1), HopStats(2)]; m.hops[0].addr = "192.168.1.1"
    for i in range(30):
        for h in m.hops + [m.dest]:
            h.add(None if i % 5 == 0 else 12.0, None, i)
    h = History(tmp_path / "x.db", autoflush=False)
    h.add("example.com", 0, 11.0)
    res = {"gateway": tests.Result("gateway", "Router", tests.PASS, "ok", ["detail line"], {"a": 1})}
    text = build_report(Settings(), [m], res, [("ok", "fine")], h, system=False)
    for needle in ("PINGRATE FULL DIAGNOSTIC EXPORT", "Target: example.com", "HISTORY for example.com",
                   "detail line", "MACHINE-READABLE"):
        assert needle in text
