from photo_viewer import create_app


def test_health_succeeds_before_configuration(tmp_path):
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path)})
    response = app.test_client().get("/api/health")
    assert response.status_code == 200
    assert response.json == {"ok": True}


def test_hdmi_audio_tone_uses_first_hdmi_output(tmp_path, monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr("photo_viewer.app.shutil.which", lambda command: command)
    monkeypatch.setattr(
        "photo_viewer.app.subprocess.run",
        lambda command, **kwargs: calls.append(command) or Result(),
    )
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path)})
    response = app.test_client().post("/api/diagnostics/audio/test")

    assert response.status_code == 200
    assert response.json["ok"] is True
    assert calls[0][0:4] == ["aplay", "-q", "-D", "plughw:CARD=vc4hdmi0,DEV=0"]
    assert (tmp_path / "hdmi-audio-test.wav").exists()


def test_local_slideshow(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "sample.jpg").write_bytes(b"jpeg-placeholder")
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path / "data")})
    client = app.test_client()
    saved = client.put(
        "/api/config",
        json={"source_type": "local", "local_path": str(photos), "interval_seconds": 7},
    )
    assert saved.status_code == 200
    assert client.post("/api/refresh").json["count"] == 1
    item = client.get("/api/next").json
    assert item["interval_seconds"] == 7
    assert client.get(item["url"]).data == b"jpeg-placeholder"


def test_video_is_catalogued_and_supports_byte_ranges(tmp_path, monkeypatch):
    monkeypatch.setattr("photo_viewer.app.time.monotonic", lambda: 10.0)
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "clip.mp4").write_bytes(b"0123456789")
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path / "data")})
    client = app.test_client()
    client.put("/api/config", json={"source_type": "local", "local_path": str(photos)})
    item = client.get("/api/next").json
    assert item["kind"] == "video"
    response = client.get(item["url"], headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.data == b"2345"
    assert response.headers["Content-Range"] == "bytes 2-5/10"


def test_date_folder_filter_supports_year_and_month(tmp_path):
    photos = tmp_path / "photos"
    june = photos / "01-06-2018"
    july = photos / "14-07-2018"
    older = photos / "02-06-2017"
    for folder in (june, july, older):
        folder.mkdir(parents=True)
        (folder / "photo.jpg").write_bytes(b"photo")
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path / "data")})
    client = app.test_client()
    client.put(
        "/api/config",
        json={
            "source_type": "local",
            "local_path": str(photos),
            "date_year": 2018,
            "date_month": 6,
        },
    )

    assert client.post("/api/refresh").json["count"] == 1
    assert "01-06-2018" in client.get("/api/next").json["path"]


def test_skip_back_returns_previous_random_item(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    for name in ("one.jpg", "two.jpg", "three.jpg"):
        (photos / name).write_bytes(b"photo")
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path / "data")})
    client = app.test_client()
    client.put("/api/config", json={"source_type": "local", "local_path": str(photos)})
    first = client.get("/api/next").json["path"]
    second = client.get("/api/next").json["path"]

    assert second != first
    assert client.get("/api/next?direction=previous").json["path"] == first


def test_collage_returns_unique_images_and_excludes_videos(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    for index in range(7):
        (photos / f"photo-{index}.jpg").write_bytes(b"photo")
    (photos / "clip.mp4").write_bytes(b"video")
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path / "data")})
    client = app.test_client()
    client.put("/api/config", json={"source_type": "local", "local_path": str(photos)})
    response = client.get("/api/collage?count=6")
    assert response.status_code == 200
    items = response.json["items"]
    assert len(items) == 6
    assert len({item["path"] for item in items}) == 6
    assert {item["kind"] for item in items} == {"image"}


def test_local_catalogue_obeys_media_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("photo_viewer.sources.MAX_ITEMS", 2)
    photos = tmp_path / "photos"
    nested = photos / "nested"
    nested.mkdir(parents=True)
    (nested / "one.jpg").write_bytes(b"1")
    (nested / "two.jpg").write_bytes(b"2")
    (photos / "three.jpg").write_bytes(b"3")
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path / "data")})
    client = app.test_client()
    client.put("/api/config", json={"source_type": "local", "local_path": str(photos)})
    assert client.post("/api/refresh").json["count"] == 2


def test_catalogue_survives_application_restart(tmp_path):
    photos = tmp_path / "photos"
    data = tmp_path / "data"
    photos.mkdir()
    (photos / "cached.jpg").write_bytes(b"photo")
    first = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(data)})
    first_client = first.test_client()
    first_client.put(
        "/api/config", json={"source_type": "local", "local_path": str(photos)}
    )
    assert first_client.post("/api/refresh").json["count"] == 1
    assert (data / "catalogue.json").exists()

    second = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(data)})
    assert second.test_client().get("/api/status").json["count"] == 1


def test_runtime_accepts_current_item_and_favourite(tmp_path):
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path)})
    client = app.test_client()
    assert (
        client.post(
            "/api/current",
            json={"path": "family/photo.jpg", "name": "photo.jpg", "kind": "image"},
        ).status_code
        == 200
    )
    assert client.post("/api/favourite").json == {"ok": True}
    state = client.get("/api/runtime").json
    assert state["current_name"] == "photo.jpg"
    assert state["favourite"] is True


def test_rotation_is_remembered(tmp_path):
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path)})
    client = app.test_client()
    client.post(
        "/api/current",
        json={"path": "family/photo.jpg", "name": "photo.jpg", "kind": "image"},
    )

    assert client.post("/api/rotate").json["rotation"] == 90
    assert client.post("/api/rotate").json["rotation"] == 180
    assert client.get("/api/runtime").json["rotation"] == 180
    assert (tmp_path / "rotations.json").exists()


def test_delete_requires_confirmation_and_moves_item_to_quarantine(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    original = photos / "family.jpg"
    original.write_bytes(b"photo")
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path / "data")})
    client = app.test_client()
    client.put("/api/config", json={"source_type": "local", "local_path": str(photos)})
    client.get("/api/next")
    client.post(
        "/api/current",
        json={"path": "family.jpg", "name": "family.jpg", "kind": "image"},
    )
    assert client.post("/api/delete/confirm").status_code == 409
    assert original.exists()
    assert client.post("/api/delete/request").json["delete_pending"] is True
    assert client.get("/api/runtime").json["paused"] is True
    response = client.post("/api/delete/confirm")
    assert response.status_code == 200
    assert not original.exists()
    assert (photos / "_PhotoViewerDeleted" / "family.jpg").exists()
    assert client.get("/api/status").json["count"] == 0
    assert client.get("/api/runtime").json["paused"] is False


def test_display_mode_writes_url_and_returns_to_photos(tmp_path):
    restarts = []
    app = create_app(
        {
            "TESTING": True,
            "PHOTO_VIEWER_DATA_DIR": str(tmp_path / "data"),
            "PHOTO_VIEWER_KIOSK_URL_FILE": str(tmp_path / "kiosk-url"),
            "PHOTO_VIEWER_KIOSK_CONTROL_SUPPORTED": True,
            "PHOTO_VIEWER_TERMINATE_BROWSER": lambda: restarts.append(True),
        }
    )
    client = app.test_client()
    client.put(
        "/api/config",
        json={
            "source_type": "local",
            "local_path": str(tmp_path),
            "dashboard_url": "http://homeassistant:8123/dashboard/home?kiosk",
        },
    )
    response = client.post("/api/display", json={"mode": "dashboard"})
    assert response.json["display_mode"] == "dashboard"
    assert (tmp_path / "kiosk-url").read_text().strip().startswith(
        "http://homeassistant"
    )
    assert restarts == [True]
    response = client.post("/api/display", json={"mode": "photos"})
    assert response.json["display_mode"] == "photos"
    assert (tmp_path / "kiosk-url").read_text().strip() == (
        "http://127.0.0.1:8080"
    )
    assert restarts == [True, True]
    response = client.post("/api/display", json={"mode": "collage"})
    assert response.json["display_mode"] == "collage"
    assert (tmp_path / "kiosk-url").read_text().strip() == "http://127.0.0.1:8080"
    assert restarts == [True, True, True]
    response = client.post("/api/display", json={"mode": "sleep"})
    assert response.json["display_mode"] == "sleep"
    assert restarts == [True, True, True, True]
