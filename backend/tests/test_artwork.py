import asyncio
from types import SimpleNamespace

import httpx
import pytest

from losspeaker.artwork import ArtworkCache, match_artwork, track_key, valid_image_url
from losspeaker.core import Store, Coordinator

TRACK = {"title": "Keep On Running", "artist": "Neptunica", "album": "Keep On Running"}
COVER = "https://is1-ssl.mzstatic.com/image/thumb/cover/100x100bb.jpg"
IMAGE = b"\xff\xd8\xff" + b"test-thumbnail"
RESULT = {
    "kind": "song",
    "trackName": TRACK["title"],
    "artistName": TRACK["artist"],
    "collectionName": "Keep On Running - Single",
    "artworkUrl100": COVER,
}


def coordinator(tmp_path):
    audio = SimpleNamespace(settings=SimpleNamespace(demo=True))
    c = Coordinator(audio, Store(tmp_path / "state.db"))
    c.state.active_source = "bluetooth"
    for name, value in TRACK.items():
        setattr(c.state, name, value)
    return c


async def settle(cache):
    await asyncio.gather(*list(cache.tasks))


def test_matching_rejects_wrong_artists_and_versions():
    assert match_artwork(TRACK, [RESULT]) == COVER
    assert match_artwork(TRACK, [{**RESULT, "artistName": "Other artist"}]) is None
    assert (
        match_artwork(
            TRACK, [{**RESULT, "trackName": "Keep On Running (Extended Mix)"}]
        )
        is None
    )
    assert (
        match_artwork({**TRACK, "title": "Keep On Running (Remix)"}, [RESULT]) is None
    )
    assert track_key({**TRACK, "artist": "NEPTUNICA"}) == track_key(TRACK)
    assert track_key({"title": "Bluetooth audio", "artist": "Connected device"}) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://is1-ssl.mzstatic.com/image.jpg",
        "https://127.0.0.1/image.jpg",
        "https://is1-ssl.mzstatic.com.evil.test/image",
        "https://user@is1-ssl.mzstatic.com/image",
        "https://is1-ssl.mzstatic.com:8080/image",
    ],
)
def test_untrusted_artwork_hosts_rejected(url):
    assert not valid_image_url(url)


async def test_lookup_is_nonblocking_and_persists_cover_for_offline_use(tmp_path):
    calls = []

    async def serve(request):
        calls.append(str(request.url))
        if request.url.host == "itunes.apple.com":
            return httpx.Response(200, json={"results": [RESULT]})
        return httpx.Response(200, content=IMAGE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        cache = ArtworkCache(tmp_path / "art", http, request_interval=0)
        c = coordinator(tmp_path)
        cache.update(c)
        assert c.state.artwork is None
        await settle(cache)
        expected = "/api/v1/artwork/" + track_key(TRACK)
        assert c.state.artwork == expected
        assert len(calls) == 2
        cache.update(c)
        await settle(cache)
        assert len(calls) == 2
        offline = ArtworkCache(tmp_path / "art", http, request_interval=0)
        assert offline.cached(TRACK) == expected
        assert (offline.directory / (track_key(TRACK) + ".image")).read_bytes() == IMAGE
        await cache.close()


async def test_old_lookup_cannot_replace_new_track_or_spotify_art(tmp_path):
    started, finish = asyncio.Event(), asyncio.Event()

    async def serve(request):
        if request.url.host == "itunes.apple.com":
            started.set()
            await finish.wait()
            return httpx.Response(200, json={"results": [RESULT]})
        return httpx.Response(200, content=IMAGE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        cache = ArtworkCache(tmp_path / "art", http, request_interval=0)
        c = coordinator(tmp_path)
        cache.update(c)
        await started.wait()
        c.state.active_source = "spotify"
        c.state.artwork = "https://i.scdn.co/image/spotify-cover"
        finish.set()
        await settle(cache)
        assert c.state.artwork.endswith("spotify-cover")
        assert cache.cached(TRACK)
        await cache.close()


async def test_track_change_cancels_old_lookup_and_closing_cleans_up(tmp_path):
    started = asyncio.Event()

    async def serve(request):
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        cache = ArtworkCache(tmp_path / "art", http, request_interval=0)
        c = coordinator(tmp_path)
        cache.update(c)
        await started.wait()
        first = cache.task
        c.state.title = "Next track"
        cache.update(c)
        await asyncio.sleep(0)
        assert first.cancelled()
        assert cache.task is not first
        await cache.close()
        assert not cache.tasks


@pytest.mark.parametrize("result", ["no-match", "offline", "oversize", "bad-image"])
async def test_failed_lookup_is_cached_and_does_not_change_playback(tmp_path, result):
    calls = []

    async def serve(request):
        calls.append(request)
        if result == "offline":
            raise httpx.ConnectError("Offline", request=request)
        if request.url.host == "itunes.apple.com":
            return httpx.Response(
                200, json={"results": [] if result == "no-match" else [RESULT]}
            )
        return httpx.Response(
            200,
            content=b"x" * (512 * 1024 + 1)
            if result == "oversize"
            else b"not an image",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as http:
        cache = ArtworkCache(tmp_path / "art", http, request_interval=0)
        c = coordinator(tmp_path)
        c.state.status = "playing"
        cache.update(c)
        await settle(cache)
        assert c.state.active_source == "bluetooth"
        assert c.state.status == "playing" and c.state.error is None
        assert c.state.artwork is None
        count = len(calls)
        cache.update(c)
        await settle(cache)
        assert len(calls) == count
        await cache.close()
