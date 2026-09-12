from __future__ import annotations

import io
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import ConfigError, ViewerConfig, clean_relative

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"}
MAX_IMAGES = 20_000


@dataclass(frozen=True)
class ImageItem:
    path: str
    name: str


class PhotoSource(Protocol):
    def folders(self, relative: str) -> list[str]: ...

    def images(self) -> list[ImageItem]: ...

    def open(self, relative: str) -> tuple[BinaryIO, str]: ...


class LocalSource:
    def __init__(self, config: ViewerConfig):
        self.root = Path(config.local_path).expanduser().resolve()
        self.base = self._resolve(config.base_folder)

    def _resolve(self, relative: str) -> Path:
        candidate = (self.root / clean_relative(relative)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ConfigError("Folder leaves the configured photo root")
        return candidate

    def folders(self, relative: str) -> list[str]:
        location = self._resolve(relative)
        return sorted(
            entry.name
            for entry in location.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )

    def images(self) -> list[ImageItem]:
        if not self.base.is_dir():
            raise ConfigError(f"Photo folder does not exist: {self.base}")
        result = []
        for entry in self.base.rglob("*"):
            if entry.is_file() and entry.suffix.lower() in IMAGE_SUFFIXES:
                relative = entry.relative_to(self.root).as_posix()
                result.append(ImageItem(relative, entry.name))
                if len(result) >= MAX_IMAGES:
                    break
        return result

    def open(self, relative: str) -> tuple[BinaryIO, str]:
        path = self._resolve(relative)
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ConfigError("Unsupported image type")
        return path.open("rb"), mimetypes.guess_type(path.name)[0] or "image/jpeg"


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

    def images(self) -> list[ImageItem]:
        result: list[ImageItem] = []

        def walk(relative: str) -> None:
            if len(result) >= MAX_IMAGES:
                return
            for entry in self.smbclient.scandir(self._unc(relative)):
                child = f"{relative}/{entry.name}".strip("/")
                if entry.is_dir() and not entry.name.startswith("."):
                    walk(child)
                elif (
                    entry.is_file()
                    and Path(entry.name).suffix.lower() in IMAGE_SUFFIXES
                ):
                    result.append(ImageItem(child, entry.name))
                    if len(result) >= MAX_IMAGES:
                        return

        walk(self.base_folder)
        return result

    def open(self, relative: str) -> tuple[BinaryIO, str]:
        relative = clean_relative(relative)
        if Path(relative).suffix.lower() not in IMAGE_SUFFIXES:
            raise ConfigError("Unsupported image type")
        with self.smbclient.open_file(self._unc(relative), mode="rb") as source:
            data = source.read()
        mime = mimetypes.guess_type(relative)[0] or "image/jpeg"
        return io.BytesIO(data), mime


def make_source(config: ViewerConfig) -> PhotoSource:
    return SmbSource(config) if config.source_type == "smb" else LocalSource(config)
