from photo_viewer import create_app


def test_health_succeeds_before_configuration(tmp_path):
    app = create_app({"TESTING": True, "PHOTO_VIEWER_DATA_DIR": str(tmp_path)})
    response = app.test_client().get("/api/health")
    assert response.status_code == 200
    assert response.json == {"ok": True}


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
