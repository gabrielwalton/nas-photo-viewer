from __future__ import annotations

import base64
import random
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from .config import ConfigError, ConfigStore, clean_relative, validate
from .sources import ImageItem, make_source


class Catalogue:
    def __init__(self):
        self.items: list[ImageItem] = []
        self.loaded_at = 0.0
        self.error = ""
        self.lock = threading.Lock()

    def refresh(self, store: ConfigStore, force: bool = False) -> list[ImageItem]:
        with self.lock:
            if not force and time.monotonic() - self.loaded_at < 60:
                return self.items
            try:
                self.items = make_source(store.load()).images()
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
        raise ConfigError("Invalid image identifier") from exc


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    if test_config:
        app.config.update(test_config)
    data_dir = app.config.get("PHOTO_VIEWER_DATA_DIR")
    store = ConfigStore(Path(data_dir) if data_dir else None)
    catalogue = Catalogue()

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
    def next_image():
        config = store.load()
        items = catalogue.refresh(store)
        if catalogue.error:
            return jsonify({"ok": False, "error": catalogue.error}), 503
        if not items:
            return jsonify({"ok": False, "error": "No supported images found"}), 404
        after = request.args.get("after", "")
        choices = [item for item in items if item.path != after] or items
        item = random.SystemRandom().choice(choices)
        return jsonify(
            {
                "ok": True,
                "path": item.path,
                "name": item.name,
                "url": f"/media/{encode_path(item.path)}",
                "interval_seconds": config.interval_seconds,
                "transition_seconds": config.transition_seconds,
                "fit_mode": config.fit_mode,
            }
        )

    @app.get("/media/<token>")
    def media(token: str):
        stream, mime = make_source(store.load()).open(decode_path(token))
        return send_file(stream, mimetype=mime, max_age=3600, conditional=True)

    return app
