from __future__ import annotations

import json
import os
import re
import socket
import threading
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import paho.mqtt.client as mqtt

from .config import ConfigStore, validate
from .display import DisplayController
from .runtime import RuntimeState


def _hardware_suffix() -> str:
    for path in (Path("/proc/device-tree/serial-number"), Path("/etc/machine-id")):
        try:
            value = path.read_text(encoding="utf-8", errors="ignore").strip("\x00\n ")
        except OSError:
            continue
        safe = re.sub(r"[^a-zA-Z0-9]", "", value).lower()
        if safe:
            return safe[-8:]
    return re.sub(r"[^a-z0-9]", "", socket.gethostname().lower())[-8:] or "unknown"


def device_id() -> str:
    configured = os.getenv("MANAGED_PI_DEVICE_ID", "").strip().lower()
    if configured and configured != "auto":
        return configured
    prefix = os.getenv("MANAGED_PI_DEVICE_ID_PREFIX", "managed-pi").strip().lower()
    return f"{prefix}-{_hardware_suffix()}"


class MqttBridge:
    def __init__(
        self,
        store: ConfigStore,
        runtime: RuntimeState,
        refresh: Callable[[], tuple[int, str, list[str]]],
        quarantine: Callable[[str], tuple[str, int]],
        display: DisplayController,
        source_changed: Callable[[], None],
    ):
        self.store = store
        self.runtime = runtime
        self.refresh_catalogue = refresh
        self.quarantine = quarantine
        self.display = display
        self.source_changed = source_changed
        self.host = os.getenv("MANAGED_PI_MQTT_HOST", "").strip()
        self.port = int(os.getenv("MANAGED_PI_MQTT_PORT", "1883"))
        self.device_id = device_id()
        self.device_name = os.getenv("MANAGED_PI_DEVICE_NAME", "NAS Photo Viewer")
        self.discovery_prefix = os.getenv(
            "MANAGED_PI_DISCOVERY_PREFIX", "homeassistant"
        )
        self.base = f"managed-pi/{self.device_id}/viewer"
        self.client: mqtt.Client | None = None
        self.count = 0
        self.error = ""
        self.folder_options = [""]
        self.refresh_guard = threading.Lock()
        self.refresh_running = False
        self.refresh_pending = False

    def start(self) -> None:
        if not self.host:
            return
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"{self.device_id}-viewer"
        )
        username = os.getenv("MANAGED_PI_MQTT_USERNAME")
        if username:
            client.username_pw_set(username, os.getenv("MANAGED_PI_MQTT_PASSWORD"))
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        self.client = client
        client.connect_async(self.host, self.port, keepalive=60)
        client.loop_start()

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code != 0:
            return
        client.subscribe(f"{self.base}/command/#")
        current_folder = self.store.load().base_folder
        self.folder_options = [current_folder] if current_folder else [""]
        self.publish_discovery()
        self.publish_state()
        self._refresh_async()

    def _refresh_async(self) -> None:
        with self.refresh_guard:
            if self.refresh_running:
                self.refresh_pending = True
                return
            self.refresh_running = True

        def worker() -> None:
            while True:
                self.count, self.error, self.folder_options = (
                    self.refresh_catalogue()
                )
                self.publish_discovery()
                self.publish_state()
                with self.refresh_guard:
                    if self.refresh_pending:
                        self.refresh_pending = False
                        continue
                    self.refresh_running = False
                    break

        threading.Thread(target=worker, daemon=True).start()

    def _on_message(self, _client, _userdata, message) -> None:
        command = message.topic.rsplit("/", 1)[-1]
        value = message.payload.decode(errors="replace").strip()
        try:
            if command == "pause":
                self.runtime.set_paused(value.upper() in {"ON", "PAUSE", "TRUE", "1"})
            elif command == "next":
                self.runtime.next()
            elif command == "favourite":
                self.runtime.favourite_current()
            elif command == "request_delete":
                if not self.runtime.request_delete_current():
                    raise RuntimeError("There is no current item to delete")
            elif command == "confirm_delete":
                path = self.runtime.confirm_delete_current()
                if not path:
                    raise RuntimeError(
                        "Deletion was not confirmed in time or the item changed"
                    )
                _destination, self.count = self.quarantine(path)
            elif command == "cancel_delete":
                self.runtime.cancel_delete()
            elif command == "refresh":
                self._refresh_async()
                self.runtime.next()
            elif command in {
                "interval",
                "folder",
                "fit",
                "dashboard_url",
                "dashboard_return",
            }:
                self._update_config(command, value)
                self.runtime.next()
            elif command == "mode":
                config = self.store.load()
                self.display.show(
                    value, config.dashboard_url, config.dashboard_return_minutes
                )
                self.runtime.set_display_mode(self.display.mode)
        except Exception as exc:
            self.error = str(exc)
        self.publish_state()

    def _update_config(self, command: str, value: str) -> None:
        current = self.store.load()
        raw = asdict(current)
        key = {
            "interval": "interval_seconds",
            "folder": "base_folder",
            "fit": "fit_mode",
            "dashboard_url": "dashboard_url",
            "dashboard_return": "dashboard_return_minutes",
        }[command]
        raw[key] = "" if command == "folder" and value == "/" else value
        self.store.save(validate(raw, current))
        if command == "folder":
            self.source_changed()
            self._refresh_async()

    def publish_discovery(self) -> None:
        if not self.client:
            return
        availability = f"managed-pi/{self.device_id}/availability"
        device = {
            "identifiers": [self.device_id],
            "name": self.device_name,
            "manufacturer": "Managed Pi",
            "model": "NAS Photo Viewer",
            "sw_version": "0.6.3",
        }
        state = f"{self.base}/state"
        definitions = {
            ("switch", "pause_slideshow"): {
                "name": "Pause slideshow",
                "command_topic": f"{self.base}/command/pause",
                "state_topic": state,
                "value_template": "{{ 'ON' if value_json.paused else 'OFF' }}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "icon": "mdi:pause-circle",
            },
            ("button", "next_item"): {
                "name": "Next item",
                "command_topic": f"{self.base}/command/next",
                "payload_press": "PRESS",
                "icon": "mdi:skip-next",
            },
            ("button", "favourite_item"): {
                "name": "Favourite item",
                "command_topic": f"{self.base}/command/favourite",
                "payload_press": "PRESS",
                "icon": "mdi:heart",
            },
            ("button", "request_delete_item"): {
                "name": "Delete item",
                "command_topic": f"{self.base}/command/request_delete",
                "payload_press": "PRESS",
                "icon": "mdi:delete-alert",
            },
            ("button", "confirm_delete_item"): {
                "name": "Confirm delete item",
                "command_topic": f"{self.base}/command/confirm_delete",
                "payload_press": "CONFIRM",
                "icon": "mdi:delete-check",
            },
            ("button", "cancel_delete_item"): {
                "name": "Cancel delete",
                "command_topic": f"{self.base}/command/cancel_delete",
                "payload_press": "CANCEL",
                "icon": "mdi:delete-off",
            },
            ("button", "refresh_library"): {
                "name": "Refresh library",
                "command_topic": f"{self.base}/command/refresh",
                "payload_press": "PRESS",
                "icon": "mdi:refresh",
            },
            ("number", "seconds_per_photo"): {
                "name": "Seconds per photo",
                "command_topic": f"{self.base}/command/interval",
                "state_topic": state,
                "value_template": "{{ value_json.interval_seconds }}",
                "min": 3,
                "max": 3600,
                "step": 1,
                "mode": "box",
                "unit_of_measurement": "s",
            },
            ("select", "photo_folder"): {
                "name": "Media folder",
                "command_topic": f"{self.base}/command/folder",
                "state_topic": state,
                "value_template": "{{ value_json.base_folder }}",
                "options": [folder or "/" for folder in self.folder_options] or ["/"],
                "icon": "mdi:folder-multiple-image",
            },
            ("select", "image_fit"): {
                "name": "Image fit",
                "command_topic": f"{self.base}/command/fit",
                "state_topic": state,
                "value_template": "{{ value_json.fit_mode }}",
                "options": ["contain", "cover"],
                "icon": "mdi:fit-to-screen",
            },
            ("select", "display_mode"): {
                "name": "Display mode",
                "command_topic": f"{self.base}/command/mode",
                "state_topic": state,
                "value_template": "{{ value_json.display_mode }}",
                "options": ["photos", "collage", "dashboard"],
                "icon": "mdi:monitor-dashboard",
            },
            ("text", "dashboard_url"): {
                "name": "Dashboard URL",
                "command_topic": f"{self.base}/command/dashboard_url",
                "state_topic": state,
                "value_template": "{{ value_json.dashboard_url }}",
                "mode": "text",
                "icon": "mdi:web",
            },
            ("number", "dashboard_return_minutes"): {
                "name": "Return to photos after",
                "command_topic": f"{self.base}/command/dashboard_return",
                "state_topic": state,
                "value_template": "{{ value_json.dashboard_return_minutes }}",
                "min": 0,
                "max": 1440,
                "step": 1,
                "mode": "box",
                "unit_of_measurement": "min",
                "icon": "mdi:timer-outline",
            },
            ("button", "show_photos"): {
                "name": "Show photos",
                "command_topic": f"{self.base}/command/mode",
                "payload_press": "photos",
                "icon": "mdi:image-multiple",
            },
            ("button", "show_dashboard"): {
                "name": "Show dashboard",
                "command_topic": f"{self.base}/command/mode",
                "payload_press": "dashboard",
                "icon": "mdi:view-dashboard",
            },
            ("button", "show_collage"): {
                "name": "Show collage",
                "command_topic": f"{self.base}/command/mode",
                "payload_press": "collage",
                "icon": "mdi:view-grid-plus",
            },
            ("sensor", "current_item"): {
                "name": "Current item",
                "state_topic": state,
                "value_template": "{{ value_json.current_name }}",
                "icon": "mdi:image",
            },
            ("sensor", "library_count"): {
                "name": "Library count",
                "state_topic": state,
                "value_template": "{{ value_json.library_count }}",
                "icon": "mdi:image-multiple",
            },
            ("sensor", "viewer_status"): {
                "name": "Viewer status",
                "state_topic": state,
                "value_template": "{{ value_json.status }}",
                "icon": "mdi:television-play",
            },
            ("binary_sensor", "delete_pending"): {
                "name": "Delete confirmation pending",
                "state_topic": state,
                "value_template": "{{ 'ON' if value_json.delete_pending else 'OFF' }}",
                "payload_on": "ON",
                "payload_off": "OFF",
                "device_class": "problem",
                "icon": "mdi:delete-clock",
            },
            ("sensor", "delete_pending_item"): {
                "name": "Item awaiting deletion",
                "state_topic": state,
                "value_template": "{{ value_json.delete_pending_name }}",
                "icon": "mdi:file-question",
            },
        }
        for (component, object_id), payload in definitions.items():
            entity_id = f"{component}.{self.device_id}_{object_id}".replace("-", "_")
            payload.update(
                {
                    "unique_id": f"{self.device_id}_viewer_{object_id}",
                    "default_entity_id": entity_id,
                    "availability_topic": availability,
                    "device": device,
                }
            )
            discovery_id = f"{self.device_id}/viewer_{object_id}"
            topic = f"{self.discovery_prefix}/{component}/{discovery_id}/config"
            self.client.publish(topic, json.dumps(payload), qos=1, retain=True)

    def publish_state(self) -> None:
        if not self.client:
            return
        config = self.store.load()
        snapshot = self.runtime.snapshot()
        snapshot.update(
            {
                "interval_seconds": config.interval_seconds,
                "base_folder": config.base_folder or "/",
                "fit_mode": config.fit_mode,
                "library_count": self.count,
                "error": self.error,
                "status": "error"
                if self.error
                else ("paused" if snapshot["paused"] else "playing"),
                "display_mode": self.display.mode,
                "dashboard_url": config.dashboard_url,
                "dashboard_return_minutes": config.dashboard_return_minutes,
            }
        )
        self.client.publish(
            f"{self.base}/state", json.dumps(snapshot), qos=1, retain=True
        )
