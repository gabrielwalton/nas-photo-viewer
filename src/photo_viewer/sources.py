from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import ConfigError, ViewerConfig, clean_relative

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"}
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm", ".ogv", ".ogg"}
MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES
MAX_ITEMS = 100_000
MAX_FOLDERS = 500


@dataclass(frozen=True)
class MediaItem:
    path: str
    name: str
    kind: str


class MediaSource(Protocol):
    def folders(self, relative: str) -> list[str]: ...

    def folder_options(self) -> list[str]: ...

    def media(self) -> list[MediaItem]: ...

    def open(self, relative: str) -> tuple[BinaryIO, str, int]: ...


def _kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    raise ConfigError("Unsupported media type")


class LocalSource:
    def __init__(self, config: ViewerConfig):
        self.root = Path(config.local_path).expanduser().resolve()
        self.base = self._resolve(config.base_folder)

    def _resolve(self, relative: str) -> Path:
        candidate = (self.root / clean_relative(relative)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ConfigError("Folder leaves the configured media root")
        return candidate

    def folders(self, relative: str) -> list[str]:
        location = self._resolve(relative)
        return sorted(
            entry.name
            for entry in location.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )

    def folder_options(self) -> list[str]:
        if not self.root.is_dir():
            raise ConfigError(f"Media folder does not exist: {self.root}")
        result = [""]
        for entry in self.root.rglob("*"):
            if entry.is_dir() and not entry.name.startswith("."):
                result.append(entry.relative_to(self.root).as_posix())
                if len(result) >= MAX_FOLDERS:
                    break
        return sorted(result)

    def media(self) -> list[MediaItem]:
        if not self.base.is_dir():
            raise ConfigError(f"Media folder does not exist: {self.base}")
        result = []
        for entry in self.base.rglob("*"):
            if entry.is_file() and entry.suffix.lower() in MEDIA_SUFFIXES:
                relative = entry.relative_to(self.root).as_posix()
                result.append(MediaItem(relative, entry.name, _kind(entry.name)))
                if len(result) >= MAX_ITEMS:
                    break
        return result

    def open(self, relative: str) -> tuple[BinaryIO, str, int]:
        path = self._resolve(relative)
        _kind(path.name)
        return (
            path.open("rb"),
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            path.stat().st_size,
        )


class SmbSource:
    def __init__(self, config: ViewerConfig):
        import smbclient

        self.smbclient = smbclient
        self.server = config.smb_server
        self.share = config.smb_share
        self.base_folder = config.base_folder
        smbclient.register_session(
            self.server,
            username=config.smb_username or None,
            password=config.smb_password or None,
            connection_timeout=10,
        )

    def _unc(self, relative: str) -> str:
        relative = clean_relative(relative)
        suffix = relative.replace("/", "\\")
        root = f"\\\\{self.server}\\{self.share}"
        return f"{root}\\{suffix}" if suffix else root

    def folders(self, relative: str) -> list[str]:
        return sorted(
            entry.name
            for entry in self.smbclient.scandir(self._unc(relative))
            if entry.is_dir() and not entry.name.startswith(".")
        )

    def folder_options(self) -> list[str]:
        result = [""]

        def walk(relative: str) -> None:
            if len(result) >= MAX_FOLDERS:
                return
            for entry in self.smbclient.scandir(self._unc(relative)):
                if entry.is_dir() and not entry.name.startswith("."):
                    child = f"{relative}/{entry.name}".strip("/")
                    result.append(child)
                    walk(child)
                    if len(result) >= MAX_FOLDERS:
                        return

        walk("")
        return sorted(result)

    def media(self) -> list[MediaItem]:
        result: list[MediaItem] = []

        def walk(relative: str) -> None:
            if len(result) >= MAX_ITEMS:
                return
            for entry in self.smbclient.scandir(self._unc(relative)):
                if len(result) >= MAX_ITEMS:
                    return
                child = f"{relative}/{entry.name}".strip("/")
                if entry.is_dir() and not entry.name.startswith("."):
                    walk(child)
                elif (
                    entry.is_file()
                    and Path(entry.name).suffix.lower() in MEDIA_SUFFIXES
                ):
                    result.append(MediaItem(child, entry.name, _kind(entry.name)))

        walk(self.base_folder)
        return result

    def open(self, relative: str) -> tuple[BinaryIO, str, int]:
        relative = clean_relative(relative)
        _kind(relative)
        path = self._unc(relative)
        stream = self.smbclient.open_file(path, mode="rb")
        mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        return stream, mime, self.smbclient.stat(path).st_size


def make_source(config: ViewerConfig) -> MediaSource:
    return SmbSource(config) if config.source_type == "smb" else LocalSource(config)
