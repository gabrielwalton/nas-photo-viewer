from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ViewerConfig:
    source_type: str = "local"
    local_path: str = "/opt/managed-pi/data/photos"
    smb_server: str = ""
    smb_share: str = ""
    smb_username: str = ""
    smb_password: str = ""
    base_folder: str = ""
    interval_seconds: int = 20
    fit_mode: str = "contain"
    transition_seconds: float = 1.0
    dashboard_url: str = ""
    dashboard_return_minutes: int = 0

    @property
    def configured(self) -> bool:
        if self.source_type == "local":
            return bool(self.local_path)
        return bool(self.smb_server and self.smb_share)

    def public(self) -> dict:
        result = asdict(self)
        result["smb_password"] = ""
        result["smb_password_saved"] = bool(self.smb_password)
        result["configured"] = self.configured
        return result


def validate(raw: dict, previous: ViewerConfig | None = None) -> ViewerConfig:
    source_type = str(raw.get("source_type", "local")).strip().lower()
    if source_type not in {"local", "smb"}:
        raise ConfigError("Source type must be local or smb")

    try:
        interval = int(raw.get("interval_seconds", 20))
        transition = float(raw.get("transition_seconds", 1.0))
        dashboard_return = int(raw.get("dashboard_return_minutes", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("Timing values must be numbers") from exc
    if not 3 <= interval <= 3600:
        raise ConfigError("Display duration must be between 3 and 3600 seconds")
    if not 0 <= transition <= 10:
        raise ConfigError("Fade duration must be between 0 and 10 seconds")
    if not 0 <= dashboard_return <= 1440:
        raise ConfigError("Dashboard return time must be between 0 and 1440 minutes")

    dashboard_url = str(raw.get("dashboard_url", "")).strip()
    if dashboard_url and not dashboard_url.startswith(("http://", "https://")):
        raise ConfigError("Dashboard URL must start with http:// or https://")

    fit_mode = str(raw.get("fit_mode", "contain")).strip().lower()
    if fit_mode not in {"contain", "cover"}:
        raise ConfigError("Fit mode must be contain or cover")

    server = str(raw.get("smb_server", "")).strip().strip("\\/")
    share = str(raw.get("smb_share", "")).strip().strip("\\/")
    if any(char in server for char in "\\/"):
        raise ConfigError("NAS server must be a hostname or IP address")
    if any(char in share for char in "\\/"):
        raise ConfigError("SMB share must be a single share name")

    supplied_password = str(raw.get("smb_password", ""))
    password = supplied_password or (previous.smb_password if previous else "")
    return ViewerConfig(
        source_type=source_type,
        local_path=str(raw.get("local_path", "")).strip(),
        smb_server=server,
        smb_share=share,
        smb_username=str(raw.get("smb_username", "")).strip(),
        smb_password=password,
        base_folder=clean_relative(str(raw.get("base_folder", ""))),
        interval_seconds=interval,
        fit_mode=fit_mode,
        transition_seconds=transition,
        dashboard_url=dashboard_url,
        dashboard_return_minutes=dashboard_return,
    )


def clean_relative(value: str) -> str:
    value = value.replace("\\", "/").strip(" /")
    parts = [part for part in value.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ConfigError("Folder cannot contain ..")
    return "/".join(parts)


class ConfigStore:
    def __init__(self, data_dir: Path | None = None):
        root = data_dir or Path(
            os.environ.get("PHOTO_VIEWER_DATA_DIR", "/opt/managed-pi/data/photo-viewer")
        )
        self.data_dir = root
        self.path = root / "config.json"

    def load(self) -> ViewerConfig:
        if not self.path.exists():
            return ViewerConfig()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return validate(raw)
        except (OSError, json.JSONDecodeError, ConfigError) as exc:
            raise ConfigError(f"Stored configuration is invalid: {exc}") from exc

    def save(self, config: ViewerConfig) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
