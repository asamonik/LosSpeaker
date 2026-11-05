import asyncio
import contextlib
import logging
from urllib.parse import quote

log = logging.getLogger(__name__)


def unwrap(v):
    if hasattr(v, "value"):
        return unwrap(v.value)
    if isinstance(v, dict):
        return {k: unwrap(x) for k, x in v.items()}
    if isinstance(v, list):
        return [unwrap(x) for x in v]
    return v


async def monitor(c, artwork=None):
    a = c.audio

    bluetooth_lock = asyncio.Lock()

    async def observe(source, fetch, playback_started=False):
        try:
            playing, meta, controls = await fetch()
            c.health[source] = {
                "available": True,
                "detail": "Connected" if playing else "Ready to receive",
            }
            await c.event(
                source, playing, meta, controls, playback_started=playback_started
            )
            if artwork:
                artwork.update(c)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            c.health[source] = {"available": False, "detail": str(exc)[:180]}
            if c.state.active_source == source:
                async with c.lock:
                    with contextlib.suppress(Exception):
                        await a.route(None)
                    c.state.active_source = None
                    c.state.status, c.state.error = (
                        "error",
                        f"{source}: connection lost",
                    )
            c.observed[source] = False
            c.publish()

    async def poll(source, fetch):
        while True:
            if source == "bluetooth":
                async with bluetooth_lock:
                    await observe(source, fetch)
            else:
                await observe(source, fetch)
            await asyncio.sleep(1)

    async def bluetooth_changed(started):
        async with bluetooth_lock:
            await observe("bluetooth", bluetooth, playback_started=started)

    async def spotify():
        r = await a.http.get(a.settings.spotify + "/status")
        r.raise_for_status()
        if r.status_code == 204:
            return False, {}, []  # Ready for a Spotify client; no active session yet.
        d = r.json()
        t = d.get("track") or {}
        return (
            not d.get("stopped", True) and not d.get("paused", False),
            {
                "title": t.get("name", "Spotify"),
                "artist": ", ".join(t.get("artist_names", [])),
                "album": t.get("album_name", ""),
                "artwork": t.get("album_cover_url"),
                "duration": t.get("duration", 0) / 1000,
                "position": t.get("position", 0) / 1000,
                "source_format": t.get("format"),
            },
            ["play", "pause", "next", "previous", "seek"],
        )

    async def airplay():
        d = unwrap(
            (
                await a.bus_call(
                    "org.gnome.ShairportSync",
                    "/org/gnome/ShairportSync",
                    "org.freedesktop.DBus.Properties",
                    "GetAll",
                    "s",
                    ["org.gnome.ShairportSync.RemoteControl"],
                )
            )[0]
        )
        m = d.get("Metadata", {})
        return (
            d.get("PlayerState", "").lower() == "playing",
            {
                "title": m.get("xesam:title", "AirPlay"),
                "artist": ", ".join(m.get("xesam:artist", [])),
                "album": m.get("xesam:album", ""),
                "artwork": m.get("mpris:artUrl"),
            },
            ["play", "pause", "next", "previous"] if d.get("Available") else [],
        )

    async def bluetooth():
        playing, meta, controls = await bluetooth_status(a)
        if artwork:
            meta["artwork"] = artwork.cached(meta)
        return playing, meta, controls

    async def mpd_loop():
        while True:
            try:
                d = await a.mpd("status")
                t = await a.mpd("currentsong")
                source = a.mpd_source
                other = "library" if source == "radio" else "radio"
                c.observed[other] = False
                c.health["library"] = c.health["radio"] = {
                    "available": True,
                    "detail": "MPD connected",
                }
                await c.event(
                    source,
                    d.get("state") == "play",
                    {
                        "title": t.get(
                            "title", t.get("file", "Unknown track").split("/")[-1]
                        ),
                        "artist": t.get("artist", t.get("name", "")),
                        "album": t.get("album", ""),
                        "duration": float(d.get("duration", 0)),
                        "position": float(d.get("elapsed", 0)),
                        "artwork": (
                            "/api/v1/library/art?uri="
                            + quote(t.get("file", ""), safe="")
                        )
                        if source == "library"
                        else None,
                        "source_format": d.get("audio"),
                    },
                    ["play", "pause", "next", "previous", "seek"]
                    if source == "library"
                    else ["play", "pause"],
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if c.state.active_source in ("library", "radio"):
                    async with c.lock:
                        with contextlib.suppress(Exception):
                            await a.route(None)
                        c.state.active_source = None
                        c.state.status = "error"
                        c.state.error = "MPD connection lost"
                c.observed["library"] = c.observed["radio"] = False
                for s in ("library", "radio"):
                    c.health[s] = {"available": False, "detail": str(exc)[:180]}
                c.publish()
            await asyncio.sleep(1)

    async with asyncio.TaskGroup() as tg:
        for source, fetch in [
            ("spotify", spotify),
            ("airplay", airplay),
            ("bluetooth", bluetooth),
        ]:
            tg.create_task(poll(source, fetch))
        tg.create_task(mpd_loop())
        tg.create_task(watch_bluetooth(a, bluetooth_changed))


async def bluetooth_status(audio):
    objects = unwrap(await audio.bluez_objects())
    transports = [
        i["org.bluez.MediaTransport1"]
        for i in objects.values()
        if "org.bluez.MediaTransport1" in i
    ]
    streaming = [t for t in transports if t.get("State") in ("pending", "active")]
    # An AVRCP player can say "stopped" while apps without media-session
    # integration are streaming. Transport state takes precedence in that case.
    device = next((t.get("Device") for t in streaming if t.get("Device")), None)
    players = [
        p["org.bluez.MediaPlayer1"]
        for path, p in objects.items()
        if "org.bluez.MediaPlayer1" in p
        and (not device or path.startswith(device + "/"))
    ]
    player = next(
        (p for p in players if p.get("Status") == "playing"),
        players[0] if players else {},
    )
    # Remote-control metadata can reflect Spotify Connect even when the phone
    # sends no Bluetooth audio. Only an actual A2DP transport can take ownership.
    playing = bool(streaming)
    # Acquiring a pending transport needs a consumer, so create the bridge
    # before waiting for BlueZ to report it as active.
    await audio.bluetooth_bridge(playing)
    track = player.get("Track", {})
    duration = track.get("Duration", 0)
    return (
        playing,
        {
            "title": track.get("Title") or "Bluetooth audio",
            "artist": track.get("Artist") or "Connected device",
            "album": track.get("Album", ""),
            "duration": 0 if duration == 0xFFFFFFFF else duration / 1000,
            "position": player.get("Position", 0) / 1000,
        },
        ["play", "pause", "next", "previous"] if player else [],
    )


class BluetoothChanges:
    """Recognize new playback edges even when a poll missed the pause."""

    def __init__(self):
        self.changed = asyncio.Event()
        self.started = False
        self.transports = {}
        self.players = {}

    def update(self, path, interface, properties):
        if interface == "org.bluez.MediaTransport1" and "State" in properties:
            value = properties["State"]
            previous = self.transports.get(path)
            self.transports[path] = value
            self.started |= value in ("pending", "active") and previous not in (
                "pending",
                "active",
            )
        elif interface == "org.bluez.MediaPlayer1" and "Status" in properties:
            value = properties["Status"]
            previous = self.players.get(path)
            self.players[path] = value
            self.started |= value == "playing" and previous != "playing"
        else:
            return
        self.changed.set()

    def message(self, message):
        from dbus_next import MessageType

        if message.message_type != MessageType.SIGNAL:
            return
        if (
            message.interface == "org.freedesktop.DBus.Properties"
            and message.member == "PropertiesChanged"
            and message.path.startswith("/org/bluez/")
        ):
            interface, properties, _ = unwrap(message.body)
            self.update(message.path, interface, properties)
        elif message.interface == "org.freedesktop.DBus.ObjectManager":
            if message.member == "InterfacesAdded":
                path, interfaces = unwrap(message.body)
                if path.startswith("/org/bluez/"):
                    for interface, properties in interfaces.items():
                        self.update(path, interface, properties)
            elif message.member == "InterfacesRemoved":
                path, interfaces = message.body
                for interface, states in (
                    ("org.bluez.MediaTransport1", self.transports),
                    ("org.bluez.MediaPlayer1", self.players),
                ):
                    if interface in interfaces and path in states:
                        states.pop(path)
                        self.changed.set()


async def watch_bluetooth(audio, refresh):
    changes = BluetoothChanges()
    rules = [
        "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged',path_namespace='/org/bluez'",
        "type='signal',sender='org.bluez',interface='org.freedesktop.DBus.ObjectManager'",
    ]
    installed = []
    handler_installed = False
    try:
        await audio.bluez_objects()  # Ensure the shared system bus is connected.
        audio.dbus.add_message_handler(changes.message)
        handler_installed = True
        for rule in rules:
            await audio.bus_call(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "AddMatch",
                "s",
                [rule],
            )
            installed.append(rule)
        objects = unwrap(await audio.bluez_objects())
        for path, interfaces in objects.items():
            if "org.bluez.MediaTransport1" in interfaces:
                changes.transports.setdefault(
                    path, interfaces["org.bluez.MediaTransport1"].get("State")
                )
            if "org.bluez.MediaPlayer1" in interfaces:
                changes.players.setdefault(
                    path, interfaces["org.bluez.MediaPlayer1"].get("Status")
                )
        while True:
            await changes.changed.wait()
            changes.changed.clear()
            started, changes.started = changes.started, False
            await refresh(started)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Bluetooth event listener unavailable; polling remains active")
    finally:
        if handler_installed:
            audio.dbus.remove_message_handler(changes.message)
        for rule in installed:
            with contextlib.suppress(Exception):
                await audio.bus_call(
                    "org.freedesktop.DBus",
                    "/org/freedesktop/DBus",
                    "org.freedesktop.DBus",
                    "RemoveMatch",
                    "s",
                    [rule],
                )
