from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path


class DisplayController:
    def __init__(
        self,
        url_file: Path,
        local_url: str = "http://127.0.0.1:8080",
        supported: bool = False,
        terminate_browser: Callable[[], None] | None = None,
    ):
        self.url_file = url_file
        self.local_url = local_url
        self.supported = supported
        self.terminate_browser = terminate_browser or self._terminate_browser
        self.lock = threading.Lock()
        self.mode = "photos"
        self.timer: threading.Timer | None = None
        self.on_change: Callable[[], None] | None = None

    @staticmethod
    def detect_support(script: Path = Path("/usr/local/bin/managed-pi-kiosk")) -> bool:
        try:
            return "MANAGED_PI_KIOSK_URL_FILE" in script.read_text(
                encoding="utf-8", errors="ignore"
            )
        except OSError:
            return False

    @staticmethod
    def _terminate_browser() -> None:
        if os.name != "posix":
            return
        subprocess.run(
            ["pkill", "-TERM", "-x", "chromium"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def set_change_callback(self, callback: Callable[[], None]) -> None:
        self.on_change = callback

    def reset_to_photos(self) -> None:
        self._set("photos", self.local_url, 0, restart=False)

    def show(self, mode: str, dashboard_url: str, return_minutes: int = 0) -> None:
        mode = mode.strip().lower()
        if mode not in {"photos", "dashboard"}:
            raise ValueError("Display mode must be photos or dashboard")
        if mode == "dashboard":
            if not self.supported:
                raise RuntimeError(
                    "This Pi base image needs the kiosk switching update"
                )
            if not dashboard_url.startswith(("http://", "https://")):
                raise ValueError("Set a valid Home Assistant dashboard URL first")
            self._set(mode, dashboard_url, return_minutes, restart=True)
        else:
            self._set(mode, self.local_url, 0, restart=True)

    def _set(self, mode: str, url: str, return_minutes: int, restart: bool) -> None:
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            self.url_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.url_file.with_suffix(".tmp")
            temporary.write_text(url + "\n", encoding="utf-8")
            os.replace(temporary, self.url_file)
            self.mode = mode
            if mode == "dashboard" and return_minutes > 0:
                self.timer = threading.Timer(
                    return_minutes * 60,
                    self._automatic_return,
                )
                self.timer.daemon = True
                self.timer.start()
        if restart:
            self.terminate_browser()

    def _automatic_return(self) -> None:
        self._set("photos", self.local_url, 0, restart=True)
        if self.on_change:
            self.on_change()
