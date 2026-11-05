import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
import websockets
from mpd import MPDClient

log = logging.getLogger(__name__)


async def run(*args):
    proc = await asyncio.create_subprocess_exec(
        *map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 10)
    except BaseException:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise RuntimeError(err.decode().strip()[:400] or f"{args[0]} failed")
    return out.decode().strip()


def usb_playback_capabilities(text):
    if "Playback:" not in text:
        return {}
    playback = text.split("Playback:", 1)[1].split("Capture:", 1)[0]
    formats = sorted(set(re.findall(r"Format: (\w+)", playback)))
    rates = sorted(
        {
            int(rate)
            for line in re.findall(r"Rates: ([0-9, ]+)", playback)
            for rate in re.findall(r"\d+", line)
        }
    )
    return {"formats": formats, "rates": rates}


class Audio:
    def __init__(self, settings, store):
        self.settings, self.store = settings, store
        self.module = None
        self.bt_module = None
        self.http = httpx.AsyncClient(timeout=5)
        self.dbus = None
        self.mpd_lock = asyncio.Lock()
        self.mpd_source = "library"

    async def close(self):
        await self.http.aclose()
        if self.dbus:
            self.dbus.disconnect()

    async def mpd(self, command, *args):
        async with self.mpd_lock:

            def call():
                c = MPDClient()
                c.timeout = 5
                c.connect("127.0.0.1", 6600)
                try:
                    return getattr(c, command)(*args)
                finally:
                    c.disconnect()

            return await asyncio.to_thread(call)

    async def camilla(self, command, value=None):
        async with websockets.connect(self.settings.camilla, open_timeout=3) as ws:
            await ws.send(json.dumps(command if value is None else {command: value}))
            response = json.loads(await asyncio.wait_for(ws.recv(), 5))[command]
            if response["result"] != "Ok":
                raise ValueError(str(response))
            return response.get("value")

    async def route(self, source):
        if self.settings.demo:
            return
        if self.module:
            await run("pactl", "unload-module", self.module)
            self.module = None
        if source:
            if not self.store.get("output"):
                raise ValueError("Select an audio output in Settings first")
            status = await self.camilla("GetState")
            if status not in ("Running", "Paused"):
                raise RuntimeError(
                    f"Audio processor is {status}; check the DAC and profile"
                )
            bus = "mpd" if source in ("library", "radio") else source
            self.module = await run(
                "pactl",
                "load-module",
                "module-loopback",
                f"source=losspeaker.{bus}.monitor",
                "sink=losspeaker.bus",
                "latency_msec=50",
                "source_dont_move=true",
                "sink_dont_move=true",
            )

    async def bluetooth_bridge(self, playing):
        if not playing:
            if self.bt_module:
                try:
                    await run("pactl", "unload-module", self.bt_module)
                except RuntimeError:
                    pass
                self.bt_module = None
            return
        # PipeWire 1.x exposes received A2DP as a playback stream rather than
        # an Audio/Source on some versions. Keep it on its own source bus;
        # otherwise session policy can attach it to the first (AirPlay) sink.
        streams = json.loads(await run("pactl", "--format=json", "list", "sink-inputs"))
        incoming = [
            stream
            for stream in streams
            if stream.get("properties", {})
            .get("node.name", "")
            .startswith("bluez_input.")
        ]
        if incoming:
            sinks = json.loads(await run("pactl", "--format=json", "list", "sinks"))
            target = next(
                s["index"] for s in sinks if s["name"] == "losspeaker.bluetooth"
            )
            for stream in incoming:
                if str(stream["sink"]) != str(target):
                    await run(
                        "pactl",
                        "move-sink-input",
                        stream["index"],
                        "losspeaker.bluetooth",
                    )
            return
        if not self.bt_module:
            sources = json.loads(await run("pactl", "--format=json", "list", "sources"))
            source = next(
                (x["name"] for x in sources if x["name"].startswith("bluez_input.")),
                None,
            )
            if source:
                self.bt_module = await run(
                    "pactl",
                    "load-module",
                    "module-loopback",
                    f"source={source}",
                    "sink=losspeaker.bluetooth",
                    "latency_msec=100",
                    "source_dont_move=true",
                    "sink_dont_move=true",
                )

    async def volume(self, value):
        if not self.settings.demo:
            await run("pactl", "set-sink-volume", "losspeaker.bus", f"{value}%")

    async def control(self, source, action, position=None):
        if self.settings.demo:
            return
        if source in ("library", "radio"):
            if action == "pause":
                await self.mpd("pause", 1)
            elif action == "play":
                await self.mpd("pause", 0)
            elif action == "seek":
                await self.mpd("seekcur", position)
            elif action == "stop":
                await self.mpd("stop")
            else:
                await self.mpd({"next": "next", "previous": "previous"}[action])
        elif source == "spotify":
            endpoint = {
                "play": "resume",
                "pause": "pause",
                "stop": "pause",
                "next": "next",
                "previous": "prev",
                "seek": "seek",
            }[action]
            r = await self.http.post(
                f"{self.settings.spotify}/player/{endpoint}",
                json={"position": int(position * 1000)} if action == "seek" else {},
            )
            r.raise_for_status()
        elif source == "airplay":
            member = {
                "play": "Play",
                "pause": "Pause",
                "stop": "Stop",
                "next": "Next",
                "previous": "Previous",
            }[action]
            await self.bus_call(
                "org.gnome.ShairportSync",
                "/org/gnome/ShairportSync",
                "org.gnome.ShairportSync.RemoteControl",
                member,
            )
        elif source == "bluetooth":
            objects = await self.bluez_objects()
            member = {
                "play": "Play",
                "pause": "Pause",
                "stop": "Stop",
                "next": "Next",
                "previous": "Previous",
            }[action]
            for path, interfaces in objects.items():
                if "org.bluez.MediaPlayer1" in interfaces:
                    await self.bus_call(
                        "org.bluez", path, "org.bluez.MediaPlayer1", member
                    )
                    return
            raise ValueError("The Bluetooth device does not expose media controls")

    async def bus_call(
        self, destination, path, interface, member, signature="", body=None
    ):
        from dbus_next import BusType, Message, MessageType
        from dbus_next.aio import MessageBus

        if not self.dbus:
            self.dbus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        reply = await asyncio.wait_for(
            self.dbus.call(
                Message(
                    destination=destination,
                    path=path,
                    interface=interface,
                    member=member,
                    signature=signature,
                    body=body or [],
                )
            ),
            5,
        )
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(str(reply.body))
        return reply.body

    async def bluez_objects(self):
        return (
            await self.bus_call(
                "org.bluez",
                "/",
                "org.freedesktop.DBus.ObjectManager",
                "GetManagedObjects",
            )
        )[0]

    async def outputs(self):
        if self.settings.demo:
            return [{"id": "hw:CARD=Demo,DEV=0", "name": "Demo USB DAC · simulated"}]
        text = await run("aplay", "-l")
        devices = []
        for card, name, device in re.findall(
            r"card \d+: (\w+) \[([^\]]+)\], device (\d+):", text
        ):
            entry = {"id": f"hw:CARD={card},DEV={device}", "name": name}
            stream = Path("/proc/asound") / card / "stream0"
            if stream.exists():
                entry.update(usb_playback_capabilities(stream.read_text()))
            devices.append(entry)
        return devices

    async def direct(self, enabled):
        if self.settings.demo:
            return
        await self.mpd("stop")
        await self.mpd("disableoutput", 0)
        await self.mpd("disableoutput", 1)
        if enabled:
            await run("systemctl", "--user", "stop", "losspeaker-dsp")
            await self.mpd("enableoutput", 1)
        else:
            await run("systemctl", "--user", "start", "losspeaker-dsp")
            for _ in range(30):
                try:
                    if await self.camilla("GetState") in ("Running", "Paused"):
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("CamillaDSP did not recover")
            await self.mpd("enableoutput", 0)
