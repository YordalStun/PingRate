from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "PingRate"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path() -> Path:
    return data_dir() / "settings.json"


@dataclass
class Settings:
    targets: list[str] = field(default_factory=lambda: ["1.1.1.1", "google.com"])
    interval: float = 1.0
    auto_diag: bool = True
    outage_seconds: int = 10
    cooldown_minutes: int = 5
    echo_server: str = ""            # host:port of tools/echo_server.py, optional
    websites: list[str] = field(default_factory=lambda: [
        "https://www.google.com", "https://www.cloudflare.com", "https://www.wikipedia.org",
        "https://www.amazon.com", "https://www.youtube.com"])
    sched_minutes: int = 0           # run diagnostics in the background every N minutes (0 = off)
    sched_heavy: bool = False        # include speed + video tests (uses bandwidth) in scheduled runs
    video_url: str = ""              # blank = built-in list of sample videos

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
            s = cls()
            for k, v in data.items():
                if hasattr(s, k):
                    setattr(s, k, v)
            return s
        except Exception:
            return cls()

    def save(self) -> None:
        try:
            _path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except Exception:
            pass
