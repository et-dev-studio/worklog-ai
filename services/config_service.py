from __future__ import annotations

from pathlib import Path
import tomllib


class ConfigService:
    def __init__(self, path: str = "config/config.toml"):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {"lunch": {"time": "13:00"}, "evening": {"time": "18:00"}}
        with self.path.open("rb") as f:
            return tomllib.load(f)
