import io
import wave
import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient
from losspeaker.core import Settings
from losspeaker.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOSSPEAKER_DEMO", "1")
    monkeypatch.setenv("LOSSPEAKER_DATA", str(tmp_path))
    with TestClient(create_app(Settings())) as c:
        yield c


def unlock(c):
    assert c.post("/api/v1/admin/login", json={"pin": "123456"}).status_code == 200


def test_cached_artwork_is_served_locally_without_login(client):
    key = "a" * 64
    image = b"\xff\xd8\xfftest-thumbnail"
    client.app.state.artwork.save(
        key, {"found": True, "content_type": "image/jpeg"}, image
    )
    response = client.get("/api/v1/artwork/" + key)
    assert response.status_code == 200
    assert response.content == image
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_completed_bluetooth_lookup_pushes_cover_to_an_open_websocket(client):
    from test_artwork import TRACK, RESULT, IMAGE

    coordinator = client.app.state.coordinator
    cache = client.app.state.artwork

    async def start_track():
        coordinator.state.active_source = "bluetooth"
        coordinator.state.artwork = None
        for name, value in TRACK.items():
            setattr(coordinator.state, name, value)
        coordinator.publish()

    async def finish_lookup():
        def serve(request):
            if request.url.host == "itunes.apple.com":
                return httpx.Response(200, json={"results": [RESULT]})
            return httpx.Response(200, content=IMAGE)

        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
            cache.http = http
            cache.update(coordinator)
            await asyncio.gather(*list(cache.tasks))

    with client.websocket_connect("/ws/state") as socket:
        socket.receive_json()
        client.portal.call(start_track)
        assert socket.receive_json()["player"]["artwork"] is None
        client.portal.call(finish_lookup)
        player = socket.receive_json()["player"]
        assert player["title"] == TRACK["title"]
        assert player["artwork"].startswith("/api/v1/artwork/")
        assert client.get(player["artwork"]).content == IMAGE


def test_artwork_endpoint_rejects_unknown_keys_and_missing_files(client):
    for key in ("unknown", "b" * 64, "..%2Fstate.db", "https:%2F%2Fexample.com"):
        assert client.get("/api/v1/artwork/" + key).status_code == 404
    key = "c" * 64
    client.app.state.artwork.save(key, {"found": True, "content_type": "image/jpeg"})
    assert client.get("/api/v1/artwork/" + key).status_code == 404


def test_admin_protection_and_origin(client):
    assert client.post("/api/v1/bluetooth/pair").status_code == 401
    assert (
        client.post(
            "/api/v1/admin/login",
            json={"pin": "123456"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    unlock(client)
    assert client.post("/api/v1/bluetooth/pair").status_code == 200
    client.post("/api/v1/admin/logout")
    assert client.post("/api/v1/bluetooth/pair").status_code == 401


def test_pin_rate_limit(client):
    for _ in range(5):
        assert (
            client.post("/api/v1/admin/login", json={"pin": "wrong"}).status_code == 401
        )
    assert client.post("/api/v1/admin/login", json={"pin": "123456"}).status_code == 429


def test_demo_playback_controls_and_real_visualizer_label(client):
    assert client.get("/api/v1/state").json()["demo"]
    r = client.post("/api/v1/sources/spotify/demo")
    assert r.json()["player"]["active_source"] == "spotify"
    assert (
        client.post("/api/v1/player/command", json={"action": "pause"}).json()[
            "player"
        ]["status"]
        == "paused"
    )
    assert client.put("/api/v1/player/volume", json={"value": 101}).status_code == 422
    with client.websocket_connect("/ws/visualizer") as ws:
        frame = ws.receive_json()
        assert frame["available"] is False
        assert frame["spectrum"] == []


def test_favourites_persist_and_invalid_stream_rejected(client):
    stations = [
        {"stationuuid": "test", "name": "A", "url_resolved": "https://example.com/live"}
    ]
    assert client.put("/api/v1/radio/favourites", json=stations).json() == stations
    assert client.get("/api/v1/radio/favourites").json() == stations
    assert (
        client.post(
            "/api/v1/radio/play", json={"uri": "file:///etc/passwd"}
        ).status_code
        == 422
    )
    assert client.get("/api/v1/library?path=../etc").status_code == 422


def test_imported_crossover_cannot_be_bypassed(client):
    unlock(client)
    profile = b"devices:\n  samplerate: 48000\n  capture: {channels: 2}\n  playback: {channels: 4}\nfilters: {}\npipeline: []\n"
    r = client.post("/api/v1/dsp/import", files={"file": ("crossover.yml", profile)})
    assert r.status_code == 200, r.text
    assert client.put("/api/v1/player/mode", json={"mode": "direct"}).status_code == 409
    body = client.get("/api/v1/dsp").json()["settings"]
    assert client.put("/api/v1/dsp", json=body).status_code == 409
    assert (
        client.put(
            "/api/v1/dsp?replace=true", json={**body, "requires_dsp": False}
        ).status_code
        == 200
    )


def test_fir_references_must_be_uploaded(client):
    unlock(client)
    profile = b"devices:\n  samplerate: 48000\n  capture: {channels: 2}\n  playback: {channels: 2}\nfilters:\n  correction:\n    type: Conv\n    parameters: {filename: /etc/shadow}\npipeline: []\n"
    assert (
        client.post(
            "/api/v1/dsp/import", files={"file": ("bad.yml", profile)}
        ).status_code
        == 422
    )
    data = io.BytesIO()
    with wave.open(data, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00\x00" * 100)
    assert (
        client.post(
            "/api/v1/dsp/fir", files={"file": ("test.wav", data.getvalue())}
        ).status_code
        == 200
    )


def test_websocket_state_and_cross_origin_rejection(client):
    with client.websocket_connect("/ws/state") as ws:
        assert ws.receive_json()["demo"]
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/ws/state", headers={"Origin": "https://evil.example"}
        ):
            pass


def test_16_bit_usb_output_format_persists_across_dsp_edits(client, monkeypatch):
    coordinator = client.app.state.coordinator

    async def outputs():
        return [
            {
                "id": "hw:CARD=Audio,DEV=0",
                "name": "AB13X USB Audio",
                "formats": ["S16_LE"],
                "rates": [48000],
            }
        ]

    monkeypatch.setattr(coordinator.audio, "outputs", outputs)
    unlock(client)
    response = client.put("/api/v1/outputs", json={"device": "hw:CARD=Audio,DEV=0"})
    assert response.status_code == 200, response.text
    assert response.json()["player"]["output_format"] == "S16_LE"
    assert client.app.state.store.get("output_format") == "S16_LE"
    body = client.get("/api/v1/dsp").json()["settings"]
    assert client.put("/api/v1/dsp", json=body).status_code == 200
    import yaml

    profile = yaml.safe_load(
        (coordinator.audio.settings.data / "camilladsp.yml").read_text()
    )
    assert profile["devices"]["playback"]["format"] == "S16_LE"
