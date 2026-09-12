from __future__ import annotations

import base64
import random
import threading
import time
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
from .mqtt import MqttBridge
from .runtime import Favourites, RuntimeState
from .sources import MediaItem, make_source


class Catalogue:
    def __init__(self):
        self.items: list[MediaItem] = []
        self.loaded_at = 0.0
        self.error = ""
        self.lock = threading.Lock()

    def refresh(self, store: ConfigStore, force: bool = False) -> list[MediaItem]:
        with self.lock:
            if self.loaded_at and not force and time.monotonic() - self.loaded_at < 60:
                return self.items
            try:
                self.items = make_source(store.load()).media()
                self.error = ""
            except Exception as exc:
                self.items = []
                self.error = str(exc)
            self.loaded_at = time.monotonic()
            return self.items

    def invalidate(self) -> None:
        self.loaded_at = 0.0


def encode_path(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def decode_path(token: str) -> str:
    try:
        padding = "=" * (-len(token) % 4)
        return clean_relative(base64.urlsafe_b64decode(token + padding).decode())
    except Exception as exc:
        raise ConfigError("Invalid media identifier") from exc


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    if test_config:
        app.config.update(test_config)
    data_dir = app.config.get("PHOTO_VIEWER_DATA_DIR")
    store = ConfigStore(Path(data_dir) if data_dir else None)
    catalogue = Catalogue()
    favourites = Favourites(store.data_dir)
    runtime = RuntimeState(favourites)

    def refresh_for_mqtt() -> tuple[int, str, list[str]]:
        items = catalogue.refresh(store, force=True)
        try:
            folders = make_source(store.load()).folder_options()
        except Exception:
            folders = [store.load().base_folder]
        return len(items), catalogue.error, folders

    bridge = MqttBridge(store, runtime, refresh_for_mqtt)

    @app.errorhandler(ConfigError)
    def config_error(exc):
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

    @app.get("/api/config")
    def get_config():
        return jsonify(store.load().public())

    @app.put("/api/config")
    def put_config():
        previous = store.load()
        config = validate(request.get_json(force=True), previous)
        store.save(config)
        catalogue.invalidate()
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
        bridge.count = len(items)
        bridge.error = catalogue.error
        try:
            bridge.folder_options = make_source(store.load()).folder_options()
            bridge.publish_discovery()
        except Exception:
            pass
        bridge.publish_state()
        return jsonify(
            {
                "ok": not catalogue.error,
                "count": len(items),
                "error": catalogue.error,
            }
        )

    @app.get("/api/status")
    def status():
        config = store.load()
        items = catalogue.refresh(store)
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
        items = catalogue.refresh(store)
        if catalogue.error:
            return jsonify({"ok": False, "error": catalogue.error}), 503
        if not items:
            return jsonify(
                {"ok": False, "error": "No supported photos or videos found"}
            ), 404
        after = request.args.get("after", "")
        choices = [item for item in items if item.path != after] or items
        item = random.SystemRandom().choice(choices)
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
            }
        )

    @app.get("/api/runtime")
    def get_runtime():
        return jsonify({"ok": True, **runtime.snapshot()})

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
