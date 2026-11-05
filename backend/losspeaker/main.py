import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
import httpx
import yaml
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    UploadFile,
    Depends,
)
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .core import Settings, Store, Coordinator, DSP, Source, SOURCES
from .audio import Audio, run
from .monitor import monitor, unwrap
from .visualizer import Visualizer
from .artwork import ArtworkCache


def create_app(settings=None):
    cfg = settings or Settings()
    store = Store(cfg.data / "state.db")
    audio = Audio(cfg, store)
    c = Coordinator(audio, store)
    visualizer = Visualizer(c)
    artwork = ArtworkCache(cfg.data / "artwork", audio.http)
    sessions, attempts = {}, {}
    pairing_task = None
    if cfg.demo and not store.get("pin"):
        store.set_pin("123456")

    @asynccontextmanager
    async def lifespan(app):
        task = None
        if cfg.demo:
            c.health = {
                s: {"available": True, "detail": "Simulated service"} for s in SOURCES
            }
            c.state.output = store.get("output", "hw:CARD=Demo,DEV=0")
        else:
            # Remove orphaned source routes from an interrupted backend before monitoring.
            try:
                modules = json.loads(
                    await run("pactl", "--format=json", "list", "modules")
                )
                for m in modules:
                    if m["name"] == "module-loopback" and any(
                        sink in m.get("argument", "")
                        for sink in ("sink=losspeaker.bus", "sink=losspeaker.bluetooth")
                    ):
                        await run("pactl", "unload-module", m["index"])
                await audio.volume(c.state.volume)
                await audio.mpd("stop")
                await audio.mpd("disableoutput", 1)
                await audio.mpd("enableoutput", 0)
            except Exception as exc:
                c.state.error = str(exc)
            try:
                from .bluetooth_agent import register

                await register(audio)
            except Exception as exc:
                c.health["bluetooth"] = {"available": False, "detail": str(exc)}
            task = asyncio.create_task(monitor(c, artwork))
        yield
        for t in (task, pairing_task, visualizer.task):
            if t:
                t.cancel()
                await asyncio.gather(t, return_exceptions=True)
        with contextlib.suppress(Exception):
            await audio.route(None)
        await artwork.close()
        await audio.close()
        store.db.close()

    app = FastAPI(title="LosSpeaker", version="0.1.0", lifespan=lifespan)
    app.state.coordinator, app.state.store = c, store
    app.state.artwork = artwork
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=os.getenv(
            "LOSSPEAKER_HOSTS", "localhost,127.0.0.1,losspeaker.local,testserver"
        ).split(","),
    )

    @app.middleware("http")
    async def same_origin(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return Response("Cross-origin writes are disabled", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return Response(
            json.dumps({"detail": str(exc)}),
            status_code=422,
            media_type="application/json",
        )

    @app.exception_handler(OSError)
    @app.exception_handler(httpx.HTTPError)
    @app.exception_handler(RuntimeError)
    async def runtime_error(request, exc):
        return Response(
            json.dumps({"detail": str(exc)}),
            status_code=503,
            media_type="application/json",
        )

    async def admin(request: Request):
        token = request.cookies.get("losspeaker_session", "")
        if sessions.get(token, 0) < time.time():
            raise HTTPException(401, "Unlock Settings with your admin PIN")

    @app.get("/api/v1/state")
    async def state():
        return c.snapshot()

    @app.get("/api/v1/health")
    async def health():
        return {"ok": all(x["available"] for x in c.health.values()), **c.snapshot()}

    @app.get("/api/v1/sources")
    async def sources():
        return c.health

    class Login(BaseModel):
        pin: str = Field(min_length=4, max_length=128)

    @app.post("/api/v1/admin/login")
    async def login(body: Login, request: Request, response: Response):
        ip = request.client.host
        failures, until = attempts.get(ip, (0, 0))
        if until > time.time():
            raise HTTPException(429, "Try again in a minute")
        if not store.verify_pin(body.pin):
            attempts[ip] = (failures + 1, time.time() + 60 if failures >= 4 else 0)
            raise HTTPException(401, "Incorrect PIN")
        attempts.pop(ip, None)
        token = secrets.token_urlsafe(32)
        sessions[token] = time.time() + 8 * 3600
        response.set_cookie(
            "losspeaker_session",
            token,
            httponly=True,
            samesite="strict",
            max_age=8 * 3600,
            secure=request.url.scheme == "https",
        )
        return {"unlocked": True}

    @app.post("/api/v1/admin/logout")
    async def logout(request: Request, response: Response):
        sessions.pop(request.cookies.get("losspeaker_session"), None)
        response.delete_cookie("losspeaker_session")
        return {"unlocked": False}

    class Command(BaseModel):
        action: str = Field(pattern="^(play|pause|stop|next|previous|seek)$")
        position: float = Field(default=0, ge=0)

    @app.post("/api/v1/player/command")
    async def command(body: Command):
        async with c.lock:
            source = c.state.active_source
            if not source:
                raise HTTPException(409, "Start a source first")
            if body.action not in c.state.controls and body.action != "stop":
                raise HTTPException(409, "This source does not support that control")
            await audio.control(source, body.action, body.position)
            if body.action == "stop":
                await audio.route(None)
                c.state.status = "idle"
                c.state.active_source = None
                c.state.controls = []
            elif cfg.demo:
                if body.action == "seek":
                    c.state.position = body.position
                elif body.action in ("play", "pause"):
                    c.state.status = "playing" if body.action == "play" else "paused"
                    c.observed[source] = body.action == "play"
            c.publish()
        return c.snapshot()

    class Volume(BaseModel):
        value: int = Field(ge=0, le=100)

    @app.put("/api/v1/player/volume")
    async def volume(body: Volume):
        await c.volume(body.value)
        return c.snapshot()

    @app.post("/api/v1/sources/{source}/demo")
    async def demo(source: Source):
        if not cfg.demo:
            raise HTTPException(404)
        c.observed[source] = False
        await c.event(
            source,
            True,
            {
                "title": "A moment of quiet",
                "artist": "LosSpeaker Sessions",
                "album": "At home, together",
                "duration": 245,
            },
            ["play", "pause", "next", "previous", "seek"],
        )
        return c.snapshot()

    @app.get("/api/v1/outputs")
    async def outputs():
        return await audio.outputs()

    class Output(BaseModel):
        device: str = Field(pattern=r"^hw:CARD=[\w-]+,DEV=\d+$")
        dop: bool = False

    @app.put("/api/v1/outputs", dependencies=[Depends(admin)])
    async def output(body: Output):
        if c.state.mode == "direct":
            raise ValueError("Exit direct mode before changing outputs")
        if store.get("custom_profile"):
            raise ValueError("Reset the imported profile before changing outputs")
        device = next(
            (x for x in await audio.outputs() if x["id"] == body.device), None
        )
        if not device:
            raise HTTPException(422, "Device is unavailable")
        supported = device.get("formats", ["S32_LE"])
        sample_format = next(
            (
                fmt
                for fmt in ("S32_LE", "S24_4_LE", "S24_3_LE", "S16_LE", "F32_LE")
                if fmt in supported
            ),
            None,
        )
        if not sample_format:
            raise ValueError("The output has no supported PCM format")
        async with c.lock:
            await audio.route(None)
            profile = DSP.model_validate(store.get("dsp", {})).config(
                body.device, sample_format
            )
            if not cfg.demo:
                await audio.camilla("ValidateConfigJson", json.dumps(profile))
                await audio.camilla("SetConfigJson", json.dumps(profile))
                await wait_dsp()
                await run(
                    "sudo",
                    "-n",
                    "/usr/local/libexec/losspeaker-helper",
                    "output",
                    body.device,
                    "yes" if body.dop else "no",
                )
            store.put("output", body.device)
            store.put("output_format", sample_format)
            c.state.output_format = sample_format
            store.put("dop", body.dop)
            (cfg.data / "camilladsp.yml").write_text(yaml.safe_dump(profile))
            c.state.output = body.device
            c.state.status = "idle"
            c.state.active_source = None
            c.state.controls = []
            c.publish()
        return c.snapshot()

    async def wait_dsp():
        for _ in range(30):
            if await audio.camilla("GetState") in ("Running", "Paused"):
                return
            await asyncio.sleep(0.1)
        raise RuntimeError("The DAC could not start this profile")

    @app.get("/api/v1/dsp")
    async def dsp():
        return {
            "settings": store.get("dsp", DSP().model_dump()),
            "custom": store.get("custom_profile", False),
        }

    @app.put("/api/v1/dsp", dependencies=[Depends(admin)])
    async def update_dsp(body: DSP, replace: bool = False):
        if store.get("custom_profile") and not replace:
            raise HTTPException(
                409,
                "Imported profile active; explicitly reset it before using EQ editors",
            )
        await apply_profile(
            body.config(
                c.state.output or "hw:CARD=Demo,DEV=0",
                store.get("output_format", "S32_LE"),
            )
        )
        store.put("dsp", body.model_dump())
        store.put("custom_profile", False)
        c.state.sample_rate = body.samplerate
        c.state.channels = body.channels
        c.publish()
        return await dsp()

    async def apply_profile(profile):
        async with c.lock:
            if c.state.mode == "direct":
                raise ValueError("Exit direct playback before editing DSP")
            old = (
                (cfg.data / "camilladsp.yml").read_text()
                if (cfg.data / "camilladsp.yml").exists()
                else None
            )
            owner = c.state.active_source
            await audio.route(None)
            try:
                if not cfg.demo:
                    if not c.state.output:
                        raise ValueError("Select an output first")
                    await audio.camilla("ValidateConfigJson", json.dumps(profile))
                    await audio.camilla("SetConfigJson", json.dumps(profile))
                    await wait_dsp()
                if old:
                    (cfg.data / "camilladsp.previous.yml").write_text(old)
                (cfg.data / "camilladsp.yml").write_text(yaml.safe_dump(profile))
            except Exception:
                if old and not cfg.demo:
                    with contextlib.suppress(Exception):
                        await audio.camilla("SetConfig", old)
                raise
            if owner:
                await audio.route(owner)

    @app.post("/api/v1/dsp/import", dependencies=[Depends(admin)])
    async def import_profile(file: UploadFile):
        raw = await file.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("Profile exceeds 1 MB")
        p = yaml.safe_load(raw)
        if not isinstance(p, dict) or not isinstance(p.get("devices"), dict):
            raise ValueError("Invalid CamillaDSP profile")
        if not isinstance(p.get("filters") or {}, dict):
            raise ValueError("Filters must be a mapping")
        devices = p["devices"]
        channels = devices.get("capture", {}).get("channels", 2)
        out_channels = devices.get("playback", {}).get("channels", 2)
        if channels != 2 or not 2 <= out_channels <= 16:
            raise ValueError("Profiles must accept stereo and output 2–16 channels")
        for f in (p.get("filters") or {}).values():
            if f.get("type") == "Conv":
                filename = f.get("parameters", {}).get("filename", "")
                safe = (cfg.data / "filters" / Path(filename).name).resolve()
                if not safe.is_relative_to(cfg.data / "filters") or not safe.is_file():
                    raise ValueError("Upload the referenced FIR WAV file first")
                f["parameters"]["filename"] = str(safe)
        devices["capture"] = {
            "type": "Pulse",
            "channels": 2,
            "device": "losspeaker.bus.monitor",
        }
        devices["playback"] = {
            "type": "Alsa",
            "channels": out_channels,
            "device": c.state.output,
            "format": store.get("output_format", "S32_LE"),
        }
        await apply_profile(p)
        store.put("custom_profile", True)
        c.state.sample_rate = devices["samplerate"]
        c.state.channels = out_channels
        c.publish()
        current = store.get("dsp", DSP().model_dump())
        current["requires_dsp"] = True
        current["samplerate"] = devices["samplerate"]
        store.put("dsp", current)
        return await dsp()

    @app.post("/api/v1/dsp/fir", dependencies=[Depends(admin)])
    async def fir(file: UploadFile):
        import io
        import wave

        raw = await file.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("FIR exceeds 16 MB")
        try:
            with wave.open(io.BytesIO(raw)) as wav:
                if wav.getnchannels() != 1:
                    raise ValueError("FIR must be a mono PCM WAV")
        except wave.Error:
            raise ValueError("Upload a PCM WAV file")
        name = Path(file.filename or "").name
        if not name.endswith(".wav") or len(name) > 100:
            raise ValueError("Expected a .wav filename")
        folder = cfg.data / "filters"
        folder.mkdir(exist_ok=True)
        (folder / name).write_bytes(raw)
        return {"filename": name}

    class Mode(BaseModel):
        mode: str = Field(pattern="^(direct|processed)$")

    @app.put("/api/v1/player/mode", dependencies=[Depends(admin)])
    async def mode(body: Mode):
        async with c.lock:
            if body.mode == c.state.mode:
                return c.snapshot()
            if body.mode == "direct":
                if store.get("custom_profile") or store.get("dsp", {}).get(
                    "requires_dsp"
                ):
                    raise HTTPException(409, "This profile requires DSP")
                if not c.state.output:
                    raise ValueError("Select an output first")
                if c.state.active_source not in (None, "library"):
                    raise ValueError(
                        "Stop wireless/radio playback before entering direct mode"
                    )
            await audio.route(None)
            try:
                await audio.direct(body.mode == "direct")
            except Exception:
                with contextlib.suppress(Exception):
                    await audio.direct(False)
                raise
            c.state.mode = body.mode
            c.state.output_format = (
                None if body.mode == "direct" else store.get("output_format", "S32_LE")
            )
            c.state.status = "idle"
            c.state.active_source = None
            c.state.controls = []
            c.observed.clear()
            c.publish()
        return c.snapshot()

    @app.get("/api/v1/library")
    async def library(path: str = "", search: str = ""):
        safe_path(path)
        if cfg.demo:
            return [
                {
                    "file": "Sessions/01 A moment of quiet.flac",
                    "title": "A moment of quiet",
                    "artist": "LosSpeaker Sessions",
                    "album": "At home, together",
                },
                {"directory": "Sessions"},
            ]
        return (
            await audio.mpd("search", "any", search)
            if search
            else await audio.mpd("lsinfo", safe_path(path))
        )

    def safe_path(path):
        if (
            Path(path).is_absolute()
            or ".." in Path(path).parts
            or urlsplit(path).scheme
        ):
            raise ValueError("Invalid music path")
        return path

    class Track(BaseModel):
        uri: str = Field(min_length=1, max_length=2048)
        play: bool = True

    @app.get("/api/v1/artwork/{key}")
    async def cached_artwork(key: str):
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise HTTPException(404, "Artwork not found")
        entry = artwork.entry(key)
        if not entry or not entry.get("found"):
            raise HTTPException(404, "Artwork not found")
        return FileResponse(
            artwork.directory / (key + ".image"),
            media_type=entry["content_type"],
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    @app.get("/api/v1/library/art")
    async def album_art(uri: str):
        safe_path(uri)
        if cfg.demo:
            raise HTTPException(404)
        data = bytearray()
        mime = "image/jpeg"
        try:
            while len(data) < 8 * 1024 * 1024:
                chunk = await audio.mpd("albumart", uri, len(data))
                block = chunk.get("binary", b"")
                if not block:
                    break
                data.extend(block)
                mime = chunk.get("type", mime)
                if len(data) >= int(chunk.get("size", 0)):
                    break
        except Exception:
            raise HTTPException(404, "Artwork unavailable")
        if not data or len(data) >= 8 * 1024 * 1024:
            raise HTTPException(404)
        if data.startswith(b"\x89PNG"):
            mime = "image/png"
        elif data.startswith(b"\xff\xd8"):
            mime = "image/jpeg"
        else:
            raise HTTPException(404)
        return Response(
            bytes(data),
            media_type=mime,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.post("/api/v1/queue")
    async def queue_add(body: Track):
        safe_path(body.uri)
        if cfg.demo:
            return await demo("library")
        audio.mpd_source = "library"
        if body.play:
            await audio.mpd("clear")
        await audio.mpd("add", body.uri)
        if body.play:
            c.observed["library"] = False
            await audio.mpd("play")
        return {"queued": True}

    @app.get("/api/v1/queue")
    async def queue():
        return [] if cfg.demo else await audio.mpd("playlistinfo")

    @app.delete("/api/v1/queue/{position}")
    async def remove_queue(position: int):
        if position < 0:
            raise ValueError("Invalid position")
        if not cfg.demo:
            await audio.mpd("delete", position)
        return {"removed": True}

    @app.get("/api/v1/playlists")
    async def playlists():
        return (
            store.get("demo_playlists", [])
            if cfg.demo
            else await audio.mpd("listplaylists")
        )

    class Playlist(BaseModel):
        name: str = Field(pattern=r"^[\w .-]{1,100}$")

    @app.post("/api/v1/playlists")
    async def save_playlist(body: Playlist):
        if cfg.demo:
            store.put(
                "demo_playlists",
                [*store.get("demo_playlists", []), {"playlist": body.name}],
            )
        else:
            await audio.mpd("save", body.name)
        return {"saved": True}

    @app.post("/api/v1/playlists/{name}/play")
    async def load_playlist(name: str):
        Playlist(name=name)
        if cfg.demo:
            return await demo("library")
        await audio.mpd("clear")
        await audio.mpd("load", name)
        audio.mpd_source = "library"
        c.observed["library"] = False
        await audio.mpd("play")
        return {"playing": True}

    @app.get("/api/v1/library/tags/{tag}")
    async def tags(tag: str):
        if tag not in ("album", "artist", "genre"):
            raise ValueError("Unknown tag")
        return [] if cfg.demo else await audio.mpd("list", tag)

    @app.post("/api/v1/library/scan", dependencies=[Depends(admin)])
    async def scan(body: Playlist):
        name = body.name
        if not cfg.demo:
            path = cfg.music / name
            if not path.is_dir() or not any(path.iterdir()):
                raise ValueError("Source is offline or empty; index retained")
            return {"job": await audio.mpd("update", name)}
        return {"job": "simulated"}

    @app.get("/api/v1/storage", dependencies=[Depends(admin)])
    async def storage():
        return store.get("storage", [])

    class Storage(BaseModel):
        name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
        kind: str = Field(pattern="^(local|usb|smb|nfs)$")
        remote: str = Field(max_length=255)
        username: str = Field(default="", max_length=100)
        password: str = Field(default="", max_length=255)

    @app.post("/api/v1/storage", dependencies=[Depends(admin)])
    async def storage_add(body: Storage):
        if not cfg.demo:
            p = await asyncio.create_subprocess_exec(
                "sudo",
                "-n",
                "/usr/local/libexec/losspeaker-helper",
                "mount",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(
                p.communicate(body.model_dump_json().encode()), 30
            )
            if p.returncode:
                raise RuntimeError(err.decode()[:300])
        items = store.get("storage", [])
        items = [x for x in items if x["name"] != body.name]
        items.append(body.model_dump(exclude={"password", "username"}))
        store.put("storage", items)
        return items

    @app.get("/api/v1/radio/favourites")
    async def favourites():
        return store.get("favourites", [])

    @app.put("/api/v1/radio/favourites")
    async def set_favourites(body: list[dict]):
        if len(body) > 500 or len(json.dumps(body)) > 500000:
            raise ValueError("Too many favourites")
        store.put("favourites", body)
        return body

    @app.get("/api/v1/radio/search")
    async def radio_search(
        name: str = "", countrycode: str = "", tag: str = "", language: str = ""
    ):
        key = hashlib.sha256(
            json.dumps([name, countrycode, tag, language]).encode()
        ).hexdigest()
        try:
            hosts = await asyncio.to_thread(
                socket.getaddrinfo,
                "all.api.radio-browser.info",
                443,
                type=socket.SOCK_STREAM,
            )
            for addr in list(dict.fromkeys(x[4][0] for x in hosts))[:3]:
                try:
                    host = await asyncio.to_thread(socket.gethostbyaddr, addr)
                    if not host[0].endswith(".api.radio-browser.info"):
                        continue
                    r = await audio.http.get(
                        "https://" + host[0] + "/json/stations/search",
                        params={
                            "name": name,
                            "countrycode": countrycode,
                            "tag": tag,
                            "language": language,
                            "limit": 60,
                            "hidebroken": "true",
                            "order": "clickcount",
                            "reverse": "true",
                        },
                        headers={
                            "User-Agent": "LosSpeaker/0.1 (Raspberry Pi audio player)"
                        },
                    )
                    r.raise_for_status()
                    result = r.json()
                    store.put("radio:" + key, result)
                    return {"stations": result, "cached": False}
                except (httpx.HTTPError, OSError):
                    continue
        except OSError:
            pass
        return {"stations": store.get("radio:" + key, []), "cached": True}

    @app.post("/api/v1/radio/play")
    async def radio_play(body: Track):
        u = urlsplit(body.uri)
        if u.scheme not in ("http", "https") or not u.hostname or u.username:
            raise ValueError("Expected an HTTP(S) stream URL")
        if cfg.demo:
            return await demo("radio")
        async with c.lock:
            if c.state.mode == "direct":
                await audio.route(None)
                await audio.direct(False)
                c.state.mode = "processed"
                c.state.output_format = store.get("output_format", "S32_LE")
                c.state.active_source = None
                c.state.controls = []
                c.publish()
        audio.mpd_source = "radio"
        c.observed["radio"] = False
        await audio.mpd("clear")
        await audio.mpd("add", body.uri)
        await audio.mpd("play")
        return {"playing": True}

    @app.get("/api/v1/bluetooth", dependencies=[Depends(admin)])
    async def bluetooth():
        if cfg.demo:
            return []
        objects = unwrap(await audio.bluez_objects())
        return [
            {"path": p, **v["org.bluez.Device1"]}
            for p, v in objects.items()
            if "org.bluez.Device1" in v
        ]

    @app.post("/api/v1/bluetooth/pair", dependencies=[Depends(admin)])
    async def pair():
        nonlocal pairing_task
        if pairing_task:
            pairing_task.cancel()
            await asyncio.gather(pairing_task, return_exceptions=True)
        if not cfg.demo:
            from dbus_next import Variant

            for prop in ("Pairable", "Discoverable"):
                await audio.bus_call(
                    "org.bluez",
                    "/org/bluez/hci0",
                    "org.freedesktop.DBus.Properties",
                    "Set",
                    "ssv",
                    ["org.bluez.Adapter1", prop, Variant("b", True)],
                )

        async def window():
            try:
                await asyncio.sleep(120)
            finally:
                if not cfg.demo:
                    from dbus_next import Variant

                    for prop in ("Pairable", "Discoverable"):
                        with contextlib.suppress(Exception):
                            await audio.bus_call(
                                "org.bluez",
                                "/org/bluez/hci0",
                                "org.freedesktop.DBus.Properties",
                                "Set",
                                "ssv",
                                ["org.bluez.Adapter1", prop, Variant("b", False)],
                            )

        audio.pairing_until = time.time() + 120
        pairing_task = asyncio.create_task(window())
        return {"pairing_until": time.time() + 120}

    @app.delete("/api/v1/bluetooth/{device}", dependencies=[Depends(admin)])
    async def forget(device: str):
        if not __import__("re").fullmatch(r"dev_[0-9A-Fa-f_]{17}", device):
            raise ValueError("Invalid device")
        if not cfg.demo:
            await audio.bus_call(
                "org.bluez",
                "/org/bluez/hci0",
                "org.bluez.Adapter1",
                "RemoveDevice",
                "o",
                ["/org/bluez/hci0/" + device],
            )
        return {"removed": True}

    async def ws_allowed(ws):
        origin = ws.headers.get("origin")
        if origin and urlsplit(origin).netloc != ws.headers.get("host"):
            await ws.close(code=1008)
            return False
        await ws.accept()
        return True

    @app.websocket("/ws/state")
    async def ws_state(ws: WebSocket):
        if not await ws_allowed(ws):
            return
        q = asyncio.Queue(maxsize=1)
        c.listeners.add(q)
        try:
            await ws.send_json(c.snapshot())
            while True:
                try:
                    value = await asyncio.wait_for(q.get(), 15)
                except TimeoutError:
                    value = c.snapshot()
                await asyncio.wait_for(ws.send_json(value), 5)
        except (WebSocketDisconnect, TimeoutError, RuntimeError):
            pass
        finally:
            c.listeners.discard(q)

    @app.websocket("/ws/visualizer")
    async def ws_visualizer(ws: WebSocket):
        if not await ws_allowed(ws):
            return
        try:
            async for frame in visualizer.frames():
                await asyncio.wait_for(ws.send_json(frame), 2)
        except (WebSocketDisconnect, TimeoutError, RuntimeError):
            pass

    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
