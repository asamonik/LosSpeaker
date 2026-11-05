import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

Source = Literal["airplay", "spotify", "bluetooth", "library", "radio"]
SOURCES = ("airplay", "spotify", "bluetooth", "library", "radio")


class Settings:
    def __init__(self):
        self.demo = os.getenv("LOSSPEAKER_DEMO", "0") == "1"
        self.data = Path(os.getenv("LOSSPEAKER_DATA", ".state")).resolve()
        self.data.mkdir(parents=True, exist_ok=True)
        self.music = Path(
            os.getenv("LOSSPEAKER_MUSIC", str(self.data / "music"))
        ).resolve()
        self.music.mkdir(parents=True, exist_ok=True)
        self.spotify = "http://127.0.0.1:3678"
        self.camilla = "ws://127.0.0.1:1234"


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key, value):
        self.db.execute(
            "INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value))
        )
        self.db.commit()

    def verify_pin(self, pin):
        saved = self.get("pin")
        if not saved:
            return False
        digest = hashlib.scrypt(
            pin.encode(), salt=bytes.fromhex(saved["salt"]), n=16384, r=8, p=1
        ).hex()
        return secrets.compare_digest(digest, saved["hash"])

    def set_pin(self, pin):
        salt = secrets.token_bytes(16)
        self.put(
            "pin",
            {
                "salt": salt.hex(),
                "hash": hashlib.scrypt(
                    pin.encode(), salt=salt, n=16384, r=8, p=1
                ).hex(),
            },
        )


class Player(BaseModel):
    active_source: Source | None = None
    status: str = "idle"
    title: str = "Ready when you are"
    artist: str = "Choose LosSpeaker in your favourite music app"
    album: str = ""
    artwork: str | None = None
    position: float = 0
    duration: float = 0
    volume: int = 35
    mode: Literal["processed", "direct"] = "processed"
    output: str | None = None
    sample_rate: int = 48000
    channels: int = 2
    source_format: str | None = None
    output_format: str | None = "S32_LE"
    error: str | None = None
    controls: list[str] = []
    revision: int = 0


class EQBand(BaseModel):
    frequency: float = Field(gt=10, lt=96000)
    gain: float = Field(default=0, ge=-24, le=12)
    q: float = Field(default=1.414, ge=0.1, le=20)
    kind: Literal["Peaking", "Lowshelf", "Highshelf"] = "Peaking"


class DSP(BaseModel):
    samplerate: Literal[44100, 48000, 88200, 96000, 176400, 192000] = 48000
    preamp: float = Field(default=-6, ge=-60, le=0)
    graphic: list[float] = Field(
        default_factory=lambda: [0] * 10, min_length=10, max_length=10
    )
    parametric: list[EQBand] = Field(default_factory=list, max_length=32)
    channels: int = Field(default=2, ge=2, le=16)
    requires_dsp: bool = False

    def config(self, device, sample_format="S32_LE"):
        if self.channels != 2:
            raise ValueError(
                "Use an imported profile with an explicit mixer for multichannel routing"
            )
        if any(not -24 <= x <= 12 for x in self.graphic):
            raise ValueError("Graphic EQ gains must be between -24 and +12 dB")
        filters = {"preamp": {"type": "Gain", "parameters": {"gain": self.preamp}}}
        for i, (freq, gain) in enumerate(
            zip([31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000], self.graphic)
        ):
            filters[f"graphic_{i}"] = {
                "type": "Biquad",
                "parameters": {
                    "type": "Peaking",
                    "freq": freq,
                    "gain": gain,
                    "q": 1.414,
                },
            }
        for i, band in enumerate(self.parametric):
            if band.frequency >= self.samplerate / 2:
                raise ValueError("Filter frequency must be below Nyquist")
            filters[f"parametric_{i}"] = {
                "type": "Biquad",
                "parameters": {
                    "type": band.kind,
                    "freq": band.frequency,
                    "gain": band.gain,
                    "q": band.q,
                },
            }
        return {
            "devices": {
                "samplerate": self.samplerate,
                "chunksize": 1024,
                "enable_rate_adjust": True,
                "resampler": {"type": "AsyncSinc", "profile": "Balanced"},
                "capture": {
                    "type": "Pulse",
                    "channels": self.channels,
                    "device": "losspeaker.bus.monitor",
                },
                "playback": {
                    "type": "Alsa",
                    "channels": self.channels,
                    "device": device,
                    "format": sample_format,
                },
            },
            "filters": filters,
            "mixers": {},
            "pipeline": [
                {
                    "type": "Filter",
                    "channels": list(range(self.channels)),
                    "names": list(filters),
                }
            ],
        }


class Coordinator:
    """Single owner; new playback edges preempt, repeated snapshots do not."""

    def __init__(self, audio, store):
        self.audio, self.store = audio, store
        saved_dsp = store.get("dsp", {})
        self.state = Player(
            output=store.get("output"),
            output_format=store.get("output_format", "S32_LE"),
            volume=store.get("volume", 35),
            sample_rate=saved_dsp.get("samplerate", 48000),
            channels=saved_dsp.get("channels", 2),
        )
        self.lock = asyncio.Lock()
        self.observed = {}
        self.health = {
            s: {"available": False, "detail": "Waiting for service"} for s in SOURCES
        }
        self.listeners = set()

    def publish(self):
        self.state.revision += 1
        for q in self.listeners:
            if q.full():
                q.get_nowait()
            q.put_nowait(self.snapshot())

    def snapshot(self):
        return {
            "player": self.state.model_dump(),
            "sources": self.health,
            "demo": self.audio.settings.demo,
        }

    async def event(
        self, source, playing, metadata=None, controls=None, *, playback_started=False
    ):
        async with self.lock:
            previous = self.observed.get(source, False)
            self.observed[source] = playing
            if playing and (
                not previous
                or (playback_started and self.state.active_source != source)
            ):
                old = self.state.active_source
                log.info("Playback takeover: %s -> %s", old, source)
                self.state.status = "switching"
                self.publish()
                try:
                    await self.audio.route(None)
                    # Library and radio share one MPD instance; the new queue
                    # has already replaced the old stream by the time we observe it.
                    if (
                        old
                        and old != source
                        and not {old, source} <= {"library", "radio"}
                    ):
                        try:
                            await self.audio.control(old, "pause")
                        except Exception:
                            pass  # Unlinked sources cannot reach the DAC.
                    if self.state.mode == "direct" and source != "library":
                        await self.audio.direct(False)
                        self.state.mode = "processed"
                        self.state.output_format = self.store.get(
                            "output_format", "S32_LE"
                        )
                    if self.state.mode != "direct":
                        await self.audio.route(source)
                    self.state.active_source = source
                    self.state.title, self.state.artist, self.state.album = (
                        "Unknown track",
                        "",
                        "",
                    )
                    self.state.artwork = None
                    self.state.source_format = None
                    self.state.position = self.state.duration = 0
                    self.state.error = None
                    self.state.controls = controls or ["play", "pause"]
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        await self.audio.route(None)
                    self.state.status, self.state.error = "error", str(exc)
                    self.state.active_source = None
                    self.publish()
                    return
            if self.state.active_source == source:
                self.state.status = "playing" if playing else "paused"
                for k, v in (metadata or {}).items():
                    if k in {
                        "title",
                        "artist",
                        "album",
                        "artwork",
                        "position",
                        "duration",
                        "source_format",
                    }:
                        setattr(self.state, k, v)
                if controls is not None:
                    self.state.controls = controls
            self.publish()

    async def volume(self, value):
        async with self.lock:
            if self.state.mode == "direct":
                raise ValueError("Direct playback has fixed volume; use your amplifier")
            await self.audio.volume(value)
            self.state.volume = value
            self.store.put("volume", value)
            self.publish()
