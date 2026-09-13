from __future__ import annotations

import json
import os
import threading
import time
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


class Rotations:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "rotations.json"
        self.lock = threading.Lock()

    def _load(self) -> dict[str, int]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {str(path): int(value) % 360 for path, value in raw.items()}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}

    def get(self, path: str) -> int:
        if not path:
            return 0
        with self.lock:
            return self._load().get(path, 0)

    def rotate_clockwise(self, path: str) -> int:
        if not path:
            return 0
        with self.lock:
            values = self._load()
            rotation = (values.get(path, 0) + 90) % 360
            if rotation:
                values[path] = rotation
            else:
                values.pop(path, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(values, indent=2) + "\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            return rotation


class RuntimeState:
    def __init__(self, favourites: Favourites, rotations: Rotations):
        self.favourites = favourites
        self.rotations = rotations
        self.lock = threading.Lock()
        self.paused = False
        self.command_sequence = 0
        self.navigation = "next"
        self.current_path = ""
        self.current_name = ""
        self.current_kind = ""
        self.display_mode = "photos"
        self.delete_path = ""
        self.delete_name = ""
        self.delete_expires_at = 0.0
        self.delete_previous_paused = False

    def _clear_delete_locked(self) -> None:
        self.delete_path = ""
        self.delete_name = ""
        self.delete_expires_at = 0.0
        self.paused = self.delete_previous_paused
        self.delete_previous_paused = False

    def snapshot(self) -> dict:
        with self.lock:
            pending = bool(
                self.delete_path and time.monotonic() < self.delete_expires_at
            )
            if self.delete_path and not pending:
                self._clear_delete_locked()
            return {
                "paused": self.paused,
                "command_sequence": self.command_sequence,
                "navigation": self.navigation,
                "current_path": self.current_path,
                "current_name": self.current_name,
                "current_kind": self.current_kind,
                "rotation": self.rotations.get(self.current_path),
                "favourite": self.favourites.contains(self.current_path),
                "display_mode": self.display_mode,
                "delete_pending": pending,
                "delete_pending_name": self.delete_name if pending else "",
            }

    def set_current(self, path: str, name: str, kind: str) -> None:
        with self.lock:
            self.current_path = path
            self.current_name = name
            self.current_kind = kind

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.paused = paused

    def navigate(self, direction: str) -> None:
        with self.lock:
            self.navigation = direction
            self.command_sequence += 1

    def next(self) -> None:
        self.navigate("next")

    def previous(self) -> None:
        self.navigate("previous")

    def set_display_mode(self, mode: str) -> None:
        with self.lock:
            self.display_mode = mode

    def favourite_current(self) -> bool:
        with self.lock:
            path = self.current_path
        return self.favourites.add(path)

    def rotate_current(self) -> int:
        with self.lock:
            path = self.current_path
            kind = self.current_kind
        if kind != "image" or not path:
            raise ValueError("The current item is not a photo")
        return self.rotations.rotate_clockwise(path)

    def request_delete_current(self, seconds: int = 30) -> bool:
        with self.lock:
            if not self.current_path:
                return False
            self.delete_path = self.current_path
            self.delete_name = self.current_name
            self.delete_expires_at = time.monotonic() + seconds
            self.delete_previous_paused = self.paused
            self.paused = True
            return True

    def confirm_delete_current(self) -> str:
        with self.lock:
            valid = (
                self.delete_path
                and self.delete_path == self.current_path
                and time.monotonic() < self.delete_expires_at
            )
            path = self.delete_path if valid else ""
            self._clear_delete_locked()
            return path

    def cancel_delete(self) -> None:
        with self.lock:
            self._clear_delete_locked()
