import json
import os

import pytest

from photo_viewer.config import ConfigError, ConfigStore, ViewerConfig, validate


def test_password_is_never_returned():
    public = ViewerConfig(smb_password="very-secret").public()
    assert public["smb_password"] == ""
    assert public["smb_password_saved"] is True


def test_blank_password_keeps_previous_password():
    previous = ViewerConfig(smb_password="existing")
    updated = validate({"smb_password": "", "interval_seconds": 20}, previous)
    assert updated.smb_password == "existing"


def test_parent_folder_is_rejected():
    with pytest.raises(ConfigError):
        validate({"base_folder": "family/../private"})


def test_visualizer_settings_are_validated():
    config = validate(
        {"visualizer_style": "tunnel", "visualizer_sensitivity": 175}
    )
    assert config.visualizer_style == "tunnel"
    assert config.visualizer_sensitivity == 175
    with pytest.raises(ConfigError):
        validate({"visualizer_style": "unknown"})


def test_store_uses_private_permissions(tmp_path):
    store = ConfigStore(tmp_path)
    store.save(ViewerConfig())
    if os.name == "posix":
        assert store.path.stat().st_mode & 0o777 == 0o600
    assert json.loads(store.path.read_text())["source_type"] == "local"


def test_environment_seeds_a_fresh_install(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTO_VIEWER_SOURCE_TYPE", "smb")
    monkeypatch.setenv("PHOTO_VIEWER_SMB_SERVER", "nas.local")
    monkeypatch.setenv("PHOTO_VIEWER_SMB_SHARE", "Pictures")
    monkeypatch.setenv("PHOTO_VIEWER_SMB_USERNAME", "viewer")
    monkeypatch.setenv("PHOTO_VIEWER_SMB_PASSWORD", "secret")
    monkeypatch.setenv("PHOTO_VIEWER_BASE_FOLDER", "Family/2026")
    monkeypatch.setenv("PHOTO_VIEWER_DASHBOARD_URL", "http://ha.local/dashboard")

    config = ConfigStore(tmp_path).load()

    assert config.source_type == "smb"
    assert config.smb_server == "nas.local"
    assert config.smb_share == "Pictures"
    assert config.smb_username == "viewer"
    assert config.smb_password == "secret"
    assert config.base_folder == "Family/2026"
    assert config.dashboard_url == "http://ha.local/dashboard"
