import asyncio
from types import SimpleNamespace
import pytest
from losspeaker.core import Store, Coordinator, DSP


class Audio:
    def __init__(self):
        self.settings = SimpleNamespace(demo=True)
        self.calls = []
        self.fail = False

    async def route(self, s):
        self.calls.append(("route", s))
        if s and self.fail:
            raise RuntimeError("DAC missing")

    async def control(self, s, a):
        self.calls.append((a, s))

    async def direct(self, on):
        self.calls.append(("direct", on))

    async def volume(self, v):
        self.calls.append(("volume", v))


@pytest.fixture
def player(tmp_path):
    store = Store(tmp_path / "db")
    a = Audio()
    return Coordinator(a, store)


async def test_takeover_unlinks_before_pause_and_never_reclaims_on_repeated_snapshot(
    player,
):
    await player.event("airplay", True, {"title": "A"})
    await player.event("spotify", True, {"title": "B"})
    assert player.state.active_source == "spotify"
    assert player.audio.calls[-3:] == [
        ("route", None),
        ("pause", "airplay"),
        ("route", "spotify"),
    ]
    await player.event("airplay", True, {"title": "Old"})
    assert player.state.title == "B"
    await player.event("airplay", False)
    assert player.state.status == "playing"


async def test_concurrent_starts_are_serialized(player):
    await asyncio.gather(player.event("airplay", True), player.event("bluetooth", True))
    assert player.state.active_source == "bluetooth"
    calls = player.audio.calls
    assert calls.index(("route", None), 2) < calls.index(("route", "bluetooth"))


async def test_bluetooth_start_reclaims_from_spotify_when_poll_missed_pause(player):
    await player.event("bluetooth", True)
    await player.event("spotify", True)
    assert player.observed["bluetooth"] is True
    player.audio.calls.clear()
    # A repeated status must not steal playback; a new Bluetooth start must.
    await player.event("bluetooth", True)
    assert player.state.active_source == "spotify"
    await player.event("bluetooth", True, playback_started=True)
    assert player.state.active_source == "bluetooth"
    assert player.audio.calls == [
        ("route", None),
        ("pause", "spotify"),
        ("route", "bluetooth"),
    ]
    await player.event("bluetooth", True, playback_started=True)
    assert len(player.audio.calls) == 3
    await player.event("spotify", True)
    assert player.state.active_source == "bluetooth"


async def test_inactive_bluetooth_event_does_not_interrupt_spotify(player):
    await player.event("spotify", True)
    await player.event("bluetooth", False, playback_started=True)
    assert player.state.active_source == "spotify"


async def test_failed_takeover_clears_owner_and_route(player):
    await player.event("spotify", True)
    player.audio.fail = True
    await player.event("bluetooth", True)
    assert player.state.active_source is None
    assert player.state.status == "error"
    assert player.audio.calls[-1] == ("route", None)


async def test_direct_library_retains_exclusive_mode_but_wireless_restores_dsp(player):
    player.state.mode = "direct"
    await player.event("library", True)
    assert player.state.mode == "direct"
    assert ("route", "library") not in player.audio.calls
    await player.event("spotify", True)
    assert player.state.mode == "processed"
    assert player.audio.calls.index(("direct", False)) < player.audio.calls.index(
        ("route", "spotify")
    )


async def test_fixed_volume_direct_and_persistent_processed_volume(player):
    await player.volume(42)
    assert player.store.get("volume") == 42
    player.state.mode = "direct"
    with pytest.raises(ValueError):
        await player.volume(80)


async def test_slow_subscriber_drops_stale_state(player):
    q = asyncio.Queue(maxsize=1)
    player.listeners.add(q)
    for _ in range(30):
        player.publish()
    assert q.qsize() == 1
    assert q.get_nowait()["player"]["revision"] == 30


def test_eq_preserves_stereo_and_checks_nyquist():
    from losspeaker.core import EQBand

    d = DSP(parametric=[EQBand(frequency=25000)])
    with pytest.raises(ValueError):
        d.config("hw:CARD=Test,DEV=0")
    d = DSP()
    p = d.config("hw:CARD=Test,DEV=0")
    assert p["devices"]["playback"]["device"] == "hw:CARD=Test,DEV=0"
    assert p["devices"]["playback"]["format"] == "S32_LE"
    assert p["filters"]["preamp"]["parameters"]["gain"] == -6


async def test_library_to_radio_does_not_pause_the_shared_mpd_instance(player):
    await player.event("library", True)
    player.audio.calls.clear()
    await player.event("radio", True)
    assert ("pause", "library") not in player.audio.calls
    assert player.state.active_source == "radio"
    assert player.state.status == "playing"


def test_usb_adapter_uses_playback_capabilities_not_microphone():
    from losspeaker.audio import usb_playback_capabilities

    text = "Playback:\n  Format: S16_LE\n  Channels: 2\n  Rates: 48000\nCapture:\n  Format: S32_LE\n  Rates: 96000\n"
    caps = usb_playback_capabilities(text)
    assert caps == {"formats": ["S16_LE"], "rates": [48000]}
    config = DSP().config("hw:CARD=Audio,DEV=0", caps["formats"][0])
    assert config["devices"]["playback"]["format"] == "S16_LE"
    assert config["devices"]["samplerate"] == 48000
