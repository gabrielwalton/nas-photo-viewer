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


def test_store_uses_private_permissions(tmp_path):
    store = ConfigStore(tmp_path)
    store.save(ViewerConfig())
    if os.name == "posix":
        assert store.path.stat().st_mode & 0o777 == 0o600
    assert json.loads(store.path.read_text())["source_type"] == "local"
