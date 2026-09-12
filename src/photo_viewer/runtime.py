from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class Favourites:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "favourites.json"
        self.lock = threading.Lock()

    def _load(self) -> set[str]:
        try:
            return set(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            return set()

    def contains(self, path: str) -> bool:
        with self.lock:
            return path in self._load()

    def add(self, path: str) -> bool:
        if not path:
            return False
        with self.lock:
            values = self._load()
            values.add(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(sorted(values), indent=2) + "\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        return True


class RuntimeState:
    def __init__(self, favourites: Favourites):
        self.favourites = favourites
        self.lock = threading.Lock()
        self.paused = False
        self.command_sequence = 0
        self.current_path = ""
        self.current_name = ""
        self.current_kind = ""

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "paused": self.paused,
                "command_sequence": self.command_sequence,
                "current_path": self.current_path,
                "current_name": self.current_name,
                "current_kind": self.current_kind,
                "favourite": self.favourites.contains(self.current_path),
            }

    def set_current(self, path: str, name: str, kind: str) -> None:
        with self.lock:
            self.current_path = path
            self.current_name = name
            self.current_kind = kind

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = paused

    def next(self) -> None:
        with self.lock:
            self.command_sequence += 1

    def favourite_current(self) -> bool:
        with self.lock:
            path = self.current_path
        return self.favourites.add(path)
