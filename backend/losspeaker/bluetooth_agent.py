"""Headless Bluetooth pairing agent with a bounded authorization window."""
# D-Bus methods use signature strings as annotations, not Python type names.
# ruff: noqa: F821

import logging
import time

from dbus_next import DBusError, Variant
from dbus_next.service import ServiceInterface, method

log = logging.getLogger(__name__)

# BlueZ authorizes incoming AVDTP using the A2DP profile UUID (110d),
# not just the remote Audio Source (110a) or local Audio Sink (110b).
AUDIO_SERVICES = frozenset(
    f"0000{short}-0000-1000-8000-00805f9b34fb"
    for short in ("110a", "110b", "110c", "110d", "110e", "110f")
)


class Agent(ServiceInterface):
    def __init__(self, audio):
        super().__init__("org.bluez.Agent1")
        self.audio = audio

    def check(self):
        if time.time() > getattr(self.audio, "pairing_until", 0):
            raise DBusError(
                "org.bluez.Error.Rejected", "Open a pairing window in LosSpeaker first"
            )

    @method()
    def Release(self):
        pass

    @method()
    def Cancel(self):
        pass

    @method()
    def RequestAuthorization(self, device: "o"):
        self.check()

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        self.check()

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        raise DBusError("org.bluez.Error.Rejected", "Legacy PIN pairing is unsupported")

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        raise DBusError(
            "org.bluez.Error.Rejected", "Use a device supporting automatic pairing"
        )

    @method()
    async def AuthorizeService(self, device: "o", uuid: "s"):
        if uuid.lower() not in AUDIO_SERVICES:
            log.warning("Rejected Bluetooth service %s for %s", uuid, device)
            raise DBusError(
                "org.bluez.Error.Rejected", "Only audio reception is allowed"
            )
        result = await self.audio.bus_call(
            "org.bluez",
            device,
            "org.freedesktop.DBus.Properties",
            "Get",
            "ss",
            ["org.bluez.Device1", "Paired"],
        )
        if not result[0].value:
            self.check()


async def register(audio):
    objects = await audio.bluez_objects()
    agent = Agent(audio)
    audio.dbus.export("/org/losspeaker/agent", agent)
    await audio.bus_call(
        "org.bluez",
        "/org/bluez",
        "org.bluez.AgentManager1",
        "RegisterAgent",
        "os",
        ["/org/losspeaker/agent", "NoInputNoOutput"],
    )
    await audio.bus_call(
        "org.bluez",
        "/org/bluez",
        "org.bluez.AgentManager1",
        "RequestDefaultAgent",
        "o",
        ["/org/losspeaker/agent"],
    )

    # The adapter enforces the time limit even if the web service crashes.
    properties = [
        ("DiscoverableTimeout", Variant("u", 120)),
        ("PairableTimeout", Variant("u", 120)),
        ("Discoverable", Variant("b", False)),
        ("Pairable", Variant("b", False)),
        ("Alias", Variant("s", "LosSpeaker")),
    ]
    # Newer BlueZ separates connectability from discoverability. Bonded
    # senders must be able to reconnect after the pairing window expires.
    if "Connectable" in objects.get("/org/bluez/hci0", {}).get(
        "org.bluez.Adapter1", {}
    ):
        properties.append(("Connectable", Variant("b", True)))
    for name, value in properties:
        await audio.bus_call(
            "org.bluez",
            "/org/bluez/hci0",
            "org.freedesktop.DBus.Properties",
            "Set",
            "ssv",
            ["org.bluez.Adapter1", name, value],
        )
