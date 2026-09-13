from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import shutil
import struct
import subprocess
import threading
import time
import wave
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    stream_with_context,
)

from .config import ConfigError, ConfigStore, clean_relative, validate
from .display import DisplayController
from .mqtt import MqttBridge
from .runtime import Favourites, Rotations, RuntimeState
from .sources import MediaItem, make_source


class Catalogue:
    def __init__(self, cache_path: Path, initial_config):
        self.cache_path = cache_path
        self.items: list[MediaItem] = []
        self.loaded_at = 0.0
        self.error = ""
        self.lock = threading.Lock()
        self._load_cache(initial_config)

    @staticmethod
    def _key(config) -> dict:
        return {
            "source_type": config.source_type,
            "local_path": config.local_path,
            "smb_server": config.smb_server,
            "smb_share": config.smb_share,
            "base_folder": config.base_folder,
        }

    def _load_cache(self, config) -> None:
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if raw.get("source") != self._key(config):
                return
            self.items = [MediaItem(**item) for item in raw.get("items", [])]
            if self.items:
                self.loaded_at = time.monotonic()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self.items = []

    def _save_cache(self, config) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        payload = {
            "source": self._key(config),
            "items": [
                {"path": item.path, "name": item.name, "kind": item.kind}
                for item in self.items
            ],
        }
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.cache_path)

    def refresh(self, store: ConfigStore, force: bool = False) -> list[MediaItem]:
        with self.lock:
            if self.loaded_at and not force and time.monotonic() - self.loaded_at < 60:
                return self.items
        try:
            config = store.load()
            items = make_source(config).media()
            if self._key(config) != self._key(store.load()):
                with self.lock:
                    return self.items
            with self.lock:
                self.items = items
                self.error = ""
                self._save_cache(config)
                self.loaded_at = time.monotonic()
                return self.items
        except Exception as exc:
            with self.lock:
                self.items = []
                self.error = str(exc)
                self.loaded_at = time.monotonic()
                return self.items

    def invalidate(self, clear: bool = False) -> None:
        with self.lock:
            self.loaded_at = 0.0
            if clear:
                self.items = []
                try:
                    self.cache_path.unlink()
                except FileNotFoundError:
                    pass

    def remove(self, path: str) -> int:
        with self.lock:
            self.items = [item for item in self.items if item.path != path]
            return len(self.items)


def encode_path(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def decode_path(token: str) -> str:
    try:
        padding = "=" * (-len(token) % 4)
        return clean_relative(base64.urlsafe_b64decode(token + padding).decode())
    except Exception as exc:
        raise ConfigError("Invalid media identifier") from exc


DATE_PATTERNS = (
    re.compile(r"^(?P<day>\d{1,2})-(?P<month>\d{1,2})-(?P<year>\d{4})$"),
    re.compile(r"^(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})$"),
)


def media_folder_date(path: str) -> tuple[int, int] | None:
    for part in Path(path.replace("\\", "/")).parts[:-1]:
        for pattern in DATE_PATTERNS:
            match = pattern.fullmatch(part)
            if not match:
                continue
            year = int(match.group("year"))
            month = int(match.group("month"))
            day = int(match.group("day"))
            if 1900 <= year <= 2200 and 1 <= month <= 12 and 1 <= day <= 31:
                return year, month
    return None


def filter_media(items: list[MediaItem], config) -> list[MediaItem]:
    if not config.date_year and not config.date_month:
        return items
    result = []
    for item in items:
        value = media_folder_date(item.path)
        if not value:
            continue
        year, month = value
        if config.date_year and year != config.date_year:
            continue
        if config.date_month and month != config.date_month:
            continue
        result.append(item)
    return result


class PlaybackHistory:
    def __init__(self):
        self.paths: list[str] = []
        self.position = -1
        self.lock = threading.Lock()

    def choose(self, items: list[MediaItem], direction: str = "next") -> MediaItem:
        by_path = {item.path: item for item in items}
        with self.lock:
            self.paths = [path for path in self.paths if path in by_path]
            self.position = min(self.position, len(self.paths) - 1)
            if direction == "previous" and self.position > 0:
                self.position -= 1
                return by_path[self.paths[self.position]]
            if direction == "previous" and self.position >= 0:
                return by_path[self.paths[self.position]]
            if direction == "next" and self.position + 1 < len(self.paths):
                self.position += 1
                return by_path[self.paths[self.position]]
            current = self.paths[self.position] if self.position >= 0 else ""
            choices = [item for item in items if item.path != current] or items
            item = random.SystemRandom().choice(choices)
            self.paths = self.paths[: self.position + 1]
            self.paths.append(item.path)
            self.position = len(self.paths) - 1
            return item

    def clear(self) -> None:
        with self.lock:
            self.paths = []
            self.position = -1


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    if test_config:
        app.config.update(test_config)
    data_dir = app.config.get("PHOTO_VIEWER_DATA_DIR")
    store = ConfigStore(Path(data_dir) if data_dir else None)
    catalogue = Catalogue(store.data_dir / "catalogue.json", store.load())
    favourites = Favourites(store.data_dir)
    rotations = Rotations(store.data_dir)
    runtime = RuntimeState(favourites, rotations)
    history = PlaybackHistory()
    kiosk_url_file = Path(
        app.config.get(
            "PHOTO_VIEWER_KIOSK_URL_FILE",
            os.environ.get(
                "MANAGED_PI_KIOSK_URL_FILE", "/opt/managed-pi/data/kiosk-url"
            ),
        )
    )
    support_override = app.config.get("PHOTO_VIEWER_KIOSK_CONTROL_SUPPORTED")
    display = DisplayController(
        kiosk_url_file,
        local_url=os.environ.get(
            "MANAGED_PI_VIEWER_URL", "http://127.0.0.1:8080"
        ),
        supported=(
            DisplayController.detect_support()
            if support_override is None
            else bool(support_override)
        ),
        terminate_browser=app.config.get("PHOTO_VIEWER_TERMINATE_BROWSER"),
    )
    display.reset_to_photos(store.load().sleep_minutes)

    def refresh_for_mqtt() -> tuple[int, str, list[str], list[str]]:
        items = catalogue.refresh(store)
        config = store.load()
        filtered = filter_media(items, config)
        years = sorted(
            {str(value[0]) for item in items if (value := media_folder_date(item.path))}
        )
        try:
            folders = make_source(config).folder_options()
        except Exception:
            folders = [config.base_folder]
        return len(filtered), catalogue.error, folders, years

    def quarantine(path: str) -> tuple[str, int]:
        destination = make_source(store.load()).quarantine(path)
        catalogue.remove(path)
        count = len(filter_media(catalogue.items, store.load()))
        runtime.next()
        return destination, count

    def source_changed() -> None:
        catalogue.invalidate(clear=True)
        history.clear()

    bridge = MqttBridge(
        store,
        runtime,
        refresh_for_mqtt,
        quarantine,
        display,
        source_changed,
    )

    def display_changed() -> None:
        runtime.set_display_mode(display.mode)
        bridge.publish_state()

    display.set_change_callback(display_changed)

    @app.errorhandler(ConfigError)
    def config_error(exc):
        return jsonify({"ok": False, "error": str(exc)}), 400

    @app.errorhandler(ValueError)
    def value_error(exc):
        return jsonify({"ok": False, "error": str(exc)}), 400

    @app.errorhandler(OSError)
    def os_error(exc):
        return jsonify({"ok": False, "error": str(exc)}), 502

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/api/diagnostics/audio")
    def audio_diagnostics():
        proc = {}
        for name in ("cards", "devices", "pcm"):
            path = Path("/proc/asound") / name
            try:
                proc[name] = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                proc[name] = ""
        eld = {}
        for path in Path("/proc/asound").glob("card*/eld*"):
            try:
                eld[str(path)] = path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                pass
        sound_devices = []
        for path in Path("/dev/snd").glob("*"):
            sound_devices.append(
                {
                    "name": path.name,
                    "readable": os.access(path, os.R_OK),
                    "writable": os.access(path, os.W_OK),
                }
            )
        return jsonify(
            {
                "ok": True,
                "proc_asound": proc,
                "hdmi_eld": eld,
                "sound_devices": sound_devices,
                "commands": {
                    command: bool(shutil.which(command))
                    for command in ("aplay", "pactl", "wpctl", "pw-cli")
                },
            }
        )

    @app.post("/api/diagnostics/audio/test")
    def audio_test():
        if not shutil.which("aplay"):
            return jsonify({"ok": False, "error": "aplay is not installed"}), 503

        tone_path = store.data_dir / "hdmi-audio-test.wav"
        sample_rate = 48_000
        duration_seconds = 2
        amplitude = 8_000
        store.data_dir.mkdir(parents=True, exist_ok=True)
        with wave.open(str(tone_path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            for sample in range(sample_rate * duration_seconds):
                value = int(
                    amplitude * math.sin(2 * math.pi * 660 * sample / sample_rate)
                )
                output.writeframesraw(struct.pack("<hh", value, value))

        result = subprocess.run(
            [
                "aplay",
                "-q",
                "-D",
                "plughw:CARD=vc4hdmi0,DEV=0",
                str(tone_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return jsonify(
            {
                "ok": result.returncode == 0,
                "returncode": result.returncode,
                "error": result.stderr.strip()[-500:],
            }
        ), (200 if result.returncode == 0 else 502)

    @app.get("/api/config")
    def get_config():
        return jsonify(store.load().public())

    @app.put("/api/config")
    def put_config():
        previous = store.load()
        config = validate(request.get_json(force=True), previous)
        store.save(config)
        source_changed = Catalogue._key(previous) != Catalogue._key(config)
        if source_changed:
            catalogue.invalidate(clear=True)
        if source_changed or (
            previous.date_year,
            previous.date_month,
        ) != (config.date_year, config.date_month):
            history.clear()
        runtime.next()
        bridge.publish_state()
        return jsonify({"ok": True, "config": config.public()})

    @app.get("/api/folders")
    def folders():
        config = store.load()
        relative = clean_relative(request.args.get("path", ""))
        return jsonify(
            {"path": relative, "folders": make_source(config).folders(relative)}
        )

    @app.post("/api/refresh")
    def refresh():
        items = catalogue.refresh(store, force=True)
        config = store.load()
        filtered = filter_media(items, config)
        bridge.count = len(filtered)
        bridge.error = catalogue.error
        try:
            bridge.folder_options = make_source(config).folder_options()
            bridge.year_options = sorted(
                {
                    str(value[0])
                    for item in items
                    if (value := media_folder_date(item.path))
                }
            )
            bridge.publish_discovery()
        except Exception:
            pass
        bridge.publish_state()
        return jsonify(
            {
                "ok": not catalogue.error,
                "count": len(filtered),
                "error": catalogue.error,
            }
        )

    @app.get("/api/status")
    def status():
        config = store.load()
        items = filter_media(catalogue.refresh(store), config)
        bridge.count = len(items)
        bridge.error = catalogue.error
        return jsonify(
            {
                "ok": not catalogue.error,
                "configured": config.configured,
                "source_type": config.source_type,
                "base_folder": config.base_folder,
                "count": len(items),
                "error": catalogue.error,
            }
        )

    @app.get("/api/next")
    def next_media():
        config = store.load()
        items = filter_media(catalogue.refresh(store), config)
        if catalogue.error:
            return jsonify({"ok": False, "error": catalogue.error}), 503
        if not items:
            return jsonify(
                {"ok": False, "error": "No supported photos or videos found"}
            ), 404
        kind = request.args.get("kind", "")
        eligible = [item for item in items if not kind or item.kind == kind]
        if not eligible:
            message = f"No {kind or 'supported'} media found"
            return jsonify({"ok": False, "error": message}), 404
        direction = request.args.get("direction", "next")
        item = history.choose(eligible, direction)
        bridge.publish_state()
        return jsonify(
            {
                "ok": True,
                "path": item.path,
                "name": item.name,
                "kind": item.kind,
                "url": f"/media/{encode_path(item.path)}",
                "interval_seconds": config.interval_seconds,
                "transition_seconds": config.transition_seconds,
                "fit_mode": config.fit_mode,
                "rotation": rotations.get(item.path),
            }
        )

    @app.get("/api/collage")
    def collage_media():
        config = store.load()
        items = filter_media(catalogue.refresh(store), config)
        if catalogue.error:
            return jsonify({"ok": False, "error": catalogue.error}), 503
        images = [item for item in items if item.kind == "image"]
        if not images:
            error = {"ok": False, "error": "No photos found for collage mode"}
            return jsonify(error), 404
        requested = request.args.get("count", type=int)
        requested = requested or random.SystemRandom().choice((5, 6))
        count = min(max(requested, 1), 6, len(images))
        selected = random.SystemRandom().sample(images, count)
        return jsonify(
            {
                "ok": True,
                "items": [
                    {
                        "path": item.path,
                        "name": item.name,
                        "kind": item.kind,
                        "url": f"/media/{encode_path(item.path)}",
                        "rotation": rotations.get(item.path),
                    }
                    for item in selected
                ],
                "interval_seconds": config.interval_seconds,
                "transition_seconds": config.transition_seconds,
                "fit_mode": config.fit_mode,
            }
        )

    @app.get("/api/runtime")
    def get_runtime():
        return jsonify({"ok": True, **runtime.snapshot()})

    @app.post("/api/display")
    def set_display():
        raw = request.get_json(force=True)
        config = store.load()
        display.show(
            str(raw.get("mode", "")),
            config.dashboard_url,
            config.dashboard_return_minutes,
            config.sleep_minutes,
        )
        runtime.set_display_mode(display.mode)
        bridge.publish_state()
        return jsonify({"ok": True, "display_mode": display.mode})

    @app.post("/api/current")
    def set_current():
        raw = request.get_json(force=True)
        runtime.set_current(
            clean_relative(str(raw.get("path", ""))),
            str(raw.get("name", ""))[:255],
            str(raw.get("kind", ""))[:16],
        )
        bridge.publish_state()
        return jsonify({"ok": True})

    @app.post("/api/favourite")
    def favourite():
        saved = runtime.favourite_current()
        bridge.publish_state()
        return jsonify({"ok": saved})

    @app.post("/api/rotate")
    def rotate():
        rotation = runtime.rotate_current()
        bridge.publish_state()
        return jsonify({"ok": True, "rotation": rotation})

    @app.post("/api/delete/request")
    def request_delete():
        pending = runtime.request_delete_current()
        bridge.publish_state()
        return jsonify({"ok": pending, "delete_pending": pending})

    @app.post("/api/delete/confirm")
    def confirm_delete():
        path = runtime.confirm_delete_current()
        if not path:
            return jsonify(
                {
                    "ok": False,
                    "error": "Deletion was not confirmed in time or the item changed",
                }
            ), 409
        destination, count = quarantine(path)
        bridge.count = count
        bridge.publish_state()
        return jsonify({"ok": True, "moved_to": destination})

    @app.post("/api/delete/cancel")
    def cancel_delete():
        runtime.cancel_delete()
        bridge.publish_state()
        return jsonify({"ok": True})

    @app.get("/media/<token>")
    def media(token: str):
        stream, mime, size = make_source(store.load()).open(decode_path(token))
        start, end, status_code = 0, size - 1, 200
        range_header = request.headers.get("Range", "")
        if range_header.startswith("bytes="):
            try:
                first, last = range_header[6:].split("-", 1)
                start = int(first) if first else 0
                end = min(int(last), size - 1) if last else size - 1
                if start < 0 or start > end or start >= size:
                    raise ValueError
                status_code = 206
            except ValueError:
                stream.close()
                return Response(
                    status=416, headers={"Content-Range": f"bytes */{size}"}
                )
        stream.seek(start)
        remaining = end - start + 1

        def chunks():
            nonlocal remaining
            try:
                while remaining:
                    data = stream.read(min(1024 * 1024, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
            finally:
                stream.close()

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Cache-Control": "private, max-age=3600",
        }
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(
            stream_with_context(chunks()), status_code, headers, mimetype=mime
        )

    bridge.start()
    return app
