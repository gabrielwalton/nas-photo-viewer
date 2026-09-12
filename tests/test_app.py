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

