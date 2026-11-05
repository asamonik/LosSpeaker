"""Best-effort Bluetooth cover lookup, isolated from playback and source handoffs."""

import asyncio
import contextlib
import hashlib
import json
import logging
import re
import time
import unicodedata
from collections import OrderedDict
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def normalize(value):
    value = unicodedata.normalize("NFKD", str(value)).casefold()
    return " ".join(
        re.findall(
            r"[^\W_]+", "".join(c for c in value if not unicodedata.combining(c))
        )
    )


def track_key(metadata):
    title, artist, album = (
        normalize(metadata.get(k, "")) for k in ("title", "artist", "album")
    )
    if (
        not title
        or not artist
        or title in ("bluetooth audio", "unknown track")
        or artist == "connected device"
    ):
        return None
    return hashlib.sha256(json.dumps([title, artist, album]).encode()).hexdigest()


def match_artwork(metadata, results):
    title, artist, album = (
        normalize(metadata.get(k, "")) for k in ("title", "artist", "album")
    )
    best, score = None, 0
    for result in results:
        if result.get("kind") != "song":
            continue
        title_score = SequenceMatcher(
            None, title, normalize(result.get("trackName", ""))
        ).ratio()
        artist_score = SequenceMatcher(
            None, artist, normalize(result.get("artistName", ""))
        ).ratio()
        if title_score < 0.86 or artist_score < 0.80:
            continue
        # Do not substitute a different mix, live performance or cover version.
        versions = {
            "remix",
            "mix",
            "live",
            "acoustic",
            "instrumental",
            "slowed",
            "sped",
        }
        if (set(title.split()) & versions) != (
            set(normalize(result.get("trackName", "")).split()) & versions
        ):
            continue
        url = result.get("artworkUrl100", "")
        if not valid_image_url(url):
            continue
        album_score = (
            SequenceMatcher(
                None, album, normalize(result.get("collectionName", ""))
            ).ratio()
            if album
            else 0
        )
        candidate = title_score * 0.55 + artist_score * 0.35 + album_score * 0.10
        if candidate > score:
            best, score = url, candidate
    return best


def valid_image_url(url):
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and (parsed.hostname or "").endswith(".mzstatic.com")
        )
    except ValueError:
        return False


def image_type(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Unsupported artwork image")


class ArtworkCache:
    def __init__(self, directory, http, *, request_interval=3.1, max_entries=256):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.http = http
        self.request_interval = request_interval
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.current_key = None
        self.task = None
        self.tasks = set()
        self.last_request = 0

    def entry(self, key):
        if not key:
            return None
        entry = self.entries.get(key)
        if entry is None:
            try:
                entry = json.loads((self.directory / (key + ".json")).read_text())
                if not isinstance(entry, dict):
                    return None
            except (OSError, ValueError):
                return None
        if not isinstance(entry.get("expires", 0), (int, float)):
            return None
        if entry.get("expires", 0) and entry["expires"] <= time.time():
            self.entries.pop(key, None)
            return None
        if entry.get("content_type") not in IMAGE_TYPES and entry.get("found"):
            return None
        if entry.get("found") and not (self.directory / (key + ".image")).is_file():
            return None
        self.entries[key] = entry
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)
        return entry

    def cached(self, metadata):
        key = track_key(metadata)
        entry = self.entry(key)
        return "/api/v1/artwork/" + key if entry and entry.get("found") else None

    def update(self, coordinator):
        state = coordinator.state
        metadata = {k: getattr(state, k) for k in ("title", "artist", "album")}
        key = track_key(metadata) if state.active_source == "bluetooth" else None
        if key != self.current_key:
            if self.task and not self.task.done():
                self.task.cancel()
            self.current_key = key
            self.task = None
        if key and self.task is None and self.entry(key) is None:
            self.task = asyncio.create_task(
                self.lookup_and_publish(coordinator, key, metadata)
            )
            self.tasks.add(self.task)
            self.task.add_done_callback(self.tasks.discard)

    def save(self, key, entry, image=None):
        if image is not None:
            target = self.directory / (key + ".image")
            temporary = target.with_suffix(".image.tmp")
            temporary.write_bytes(image)
            temporary.replace(target)
        target = self.directory / (key + ".json")
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(entry))
        temporary.replace(target)
        self.entries[key] = entry
        files = sorted(
            self.directory.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old in files[self.max_entries :]:
            old.unlink(missing_ok=True)
            old.with_suffix(".image").unlink(missing_ok=True)
            self.entries.pop(old.stem, None)

    async def lookup_and_publish(self, coordinator, key, metadata):
        try:
            await asyncio.sleep(
                max(0, self.last_request + self.request_interval - time.monotonic())
            )
            self.last_request = time.monotonic()
            response = await self.http.get(
                "https://itunes.apple.com/search",
                params={
                    "term": " ".join(
                        str(metadata.get(k, ""))[:256] for k in ("artist", "title")
                    ),
                    "country": "US",
                    "media": "music",
                    "entity": "song",
                    "limit": 15,
                },
                timeout=5,
            )
            response.raise_for_status()
            url = match_artwork(metadata, response.json().get("results", []))
            if not url:
                self.save(key, {"found": False, "expires": time.time() + 12 * 3600})
                return
            async with self.http.stream(
                "GET", url, timeout=5, follow_redirects=False
            ) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError("Artwork redirect unavailable")
                image = bytearray()
                async for chunk in response.aiter_bytes():
                    image.extend(chunk)
                    if len(image) > 512 * 1024:
                        raise ValueError("Artwork too large")
            mime = image_type(image)
            self.save(key, {"found": True, "content_type": mime}, image)
            async with coordinator.lock:
                state = coordinator.state
                if (
                    state.active_source == "bluetooth"
                    and track_key(
                        {k: getattr(state, k) for k in ("title", "artist", "album")}
                    )
                    == key
                ):
                    state.artwork = "/api/v1/artwork/" + key
                    coordinator.publish()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Covers are optional: a failed request cannot touch playback state.
            log.debug("Bluetooth artwork lookup unavailable: %s", exc)
            with contextlib.suppress(OSError):
                self.save(key, {"found": False, "expires": time.time() + 300})
        finally:
            if self.task is asyncio.current_task():
                self.task = None

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
