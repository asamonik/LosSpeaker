import json
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from dbus_next import DBusError, Variant, Message
from losspeaker.audio import Audio
from losspeaker.bluetooth_agent import Agent, register
from losspeaker.monitor import bluetooth_status, BluetoothChanges, watch_bluetooth

DEVICE = "/org/bluez/hci0/dev_00_11_22_33_44_55"


@pytest.mark.parametrize("short", ["110a", "110b", "110c", "110d", "110e", "110f"])
async def test_bonded_sender_audio_authorized_after_pairing_window(short):
    audio = SimpleNamespace(
        pairing_until=0, bus_call=AsyncMock(return_value=[Variant("b", True)])
    )
    # Call the function dbus-next dispatches, rather than its export decorator.
    await Agent.AuthorizeService.__wrapped__(
        Agent(audio), DEVICE, f"0000{short}-0000-1000-8000-00805f9b34fb"
    )
    assert audio.bus_call.call_args.args[-1] == ["org.bluez.Device1", "Paired"]


async def test_new_sender_needs_open_pairing_window_for_a2dp_profile():
    audio = SimpleNamespace(
        pairing_until=0, bus_call=AsyncMock(return_value=[Variant("b", False)])
    )
    agent = Agent(audio)
    uuid = "0000110d-0000-1000-8000-00805f9b34fb"
    with pytest.raises(DBusError, match="Open a pairing window"):
        await Agent.AuthorizeService.__wrapped__(agent, DEVICE, uuid)
    audio.pairing_until = time.time() + 120
    await Agent.AuthorizeService.__wrapped__(agent, DEVICE, uuid.upper())


@pytest.mark.parametrize("short", ["1108", "111e", "112d", "1115", "1105"])
async def test_non_music_services_rejected_even_during_pairing(short):
    audio = SimpleNamespace(pairing_until=time.time() + 120, bus_call=AsyncMock())
    with pytest.raises(DBusError, match="Only audio reception"):
        await Agent.AuthorizeService.__wrapped__(
            Agent(audio), DEVICE, f"0000{short}-0000-1000-8000-00805f9b34fb"
        )
    audio.bus_call.assert_not_awaited()


@pytest.mark.parametrize("connectable", [True, False])
async def test_registration_keeps_reconnect_separate_from_new_pairing(connectable):
    adapter = {"Connectable": Variant("b", False)} if connectable else {}
    audio = SimpleNamespace(
        bluez_objects=AsyncMock(
            return_value={"/org/bluez/hci0": {"org.bluez.Adapter1": adapter}}
        ),
        dbus=SimpleNamespace(export=lambda *_: None),
        bus_call=AsyncMock(),
    )
    await register(audio)
    settings = {
        args.args[-1][1]: args.args[-1][2].value
        for args in audio.bus_call.call_args_list
        if args.args[3] == "Set"
    }
    assert settings["Pairable"] is False
    assert settings["Discoverable"] is False
    assert settings["Alias"] == "LosSpeaker"
    if connectable:
        assert settings["Connectable"] is True
    else:
        assert "Connectable" not in settings


@pytest.mark.parametrize("transport", ["pending", "active"])
async def test_transport_streams_even_if_avrcp_player_says_stopped(transport):
    audio = SimpleNamespace(
        bluez_objects=AsyncMock(
            return_value={
                DEVICE + "/player0": {
                    "org.bluez.MediaPlayer1": {
                        "Status": Variant("s", "stopped"),
                        "Track": {"Duration": Variant("u", 0xFFFFFFFF)},
                    }
                },
                DEVICE + "/fd0": {
                    "org.bluez.MediaTransport1": {
                        "State": Variant("s", transport),
                        "Device": Variant("o", DEVICE),
                    }
                },
            }
        ),
        bluetooth_bridge=AsyncMock(),
    )
    playing, metadata, controls = await bluetooth_status(audio)
    assert playing
    assert metadata["title"] == "Bluetooth audio"
    assert metadata["duration"] == 0
    assert "pause" in controls
    audio.bluetooth_bridge.assert_awaited_once_with(True)


async def test_disconnect_releases_bridge_and_player_with_no_avrcp_supported():
    audio = SimpleNamespace(
        bluez_objects=AsyncMock(return_value={}), bluetooth_bridge=AsyncMock()
    )
    playing, _, controls = await bluetooth_status(audio)
    assert not playing
    assert controls == []
    audio.bluetooth_bridge.assert_awaited_once_with(False)
    audio.bluez_objects.return_value = {
        DEVICE + "/fd0": {"org.bluez.MediaTransport1": {"State": "active"}}
    }
    playing, _, controls = await bluetooth_status(audio)
    assert playing
    assert controls == []


async def test_remote_spotify_metadata_on_idle_bluetooth_does_not_steal():
    audio = SimpleNamespace(
        bluez_objects=AsyncMock(
            return_value={
                DEVICE + "/player0": {"org.bluez.MediaPlayer1": {"Status": "playing"}},
                DEVICE + "/fd0": {
                    "org.bluez.MediaTransport1": {"State": "idle", "Device": DEVICE}
                },
            }
        ),
        bluetooth_bridge=AsyncMock(),
    )
    playing, _, _ = await bluetooth_status(audio)
    assert not playing
    audio.bluetooth_bridge.assert_awaited_once_with(False)


def test_transport_and_remote_play_edges_are_distinct_from_repeated_status():
    changes = BluetoothChanges()
    changes.update(DEVICE + "/fd0", "org.bluez.MediaTransport1", {"State": "idle"})
    assert not changes.started
    changes.update(DEVICE + "/fd0", "org.bluez.MediaTransport1", {"State": "pending"})
    assert changes.started
    changes.started = False
    changes.update(DEVICE + "/fd0", "org.bluez.MediaTransport1", {"State": "active"})
    assert not changes.started  # Acquiring the same stream isn't a new sender.
    changes.update(DEVICE + "/player0", "org.bluez.MediaPlayer1", {"Status": "paused"})
    changes.update(DEVICE + "/player0", "org.bluez.MediaPlayer1", {"Status": "playing"})
    assert changes.started  # Resume while A2DP remains active between polls.
    changes.started = False
    changes.update(DEVICE + "/player0", "org.bluez.MediaPlayer1", {"Status": "playing"})
    changes.update(
        DEVICE + "/player0", "org.bluez.MediaPlayer1", {"Track": {"Title": "Updated"}}
    )
    assert not changes.started


async def test_bluez_signal_subscription_refreshes_and_cleans_up():
    bus = SimpleNamespace(add_message_handler=Mock(), remove_message_handler=Mock())
    audio = SimpleNamespace(
        dbus=bus, bluez_objects=AsyncMock(return_value={}), bus_call=AsyncMock()
    )
    called = asyncio.Event()
    refresh = AsyncMock(side_effect=lambda _: called.set())
    task = asyncio.create_task(watch_bluetooth(audio, refresh))
    await asyncio.sleep(0)
    handler = bus.add_message_handler.call_args.args[0]
    handler(
        Message.new_signal(
            DEVICE + "/fd0",
            "org.freedesktop.DBus.Properties",
            "PropertiesChanged",
            "sa{sv}as",
            ["org.bluez.MediaTransport1", {"State": Variant("s", "active")}, []],
        )
    )
    await asyncio.wait_for(called.wait(), 1)
    refresh.assert_awaited_once_with(True)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    bus.remove_message_handler.assert_called_once_with(handler)
    assert [call.args[3] for call in audio.bus_call.call_args_list] == [
        "AddMatch",
        "AddMatch",
        "RemoveMatch",
        "RemoveMatch",
    ]


@pytest.mark.parametrize("current_sink", [37, 43])
async def test_pipewire_received_stream_stays_on_bluetooth_bus(
    monkeypatch, current_sink
):
    run = AsyncMock(
        side_effect=[
            json.dumps(
                [
                    {
                        "index": 407,
                        "sink": current_sink,
                        "properties": {"node.name": "bluez_input.AA_BB_CC_DD_EE_FF.2"},
                    },
                    {
                        "index": 91,
                        "sink": 40,
                        "properties": {"node.name": "go-librespot"},
                    },
                ]
            ),
            json.dumps([{"index": 43, "name": "losspeaker.bluetooth"}]),
            "",
        ]
    )
    monkeypatch.setattr("losspeaker.audio.run", run)
    audio = SimpleNamespace(bt_module=None)
    await Audio.bluetooth_bridge(audio, True)
    if current_sink == 37:
        assert run.call_args.args == (
            "pactl",
            "move-sink-input",
            407,
            "losspeaker.bluetooth",
        )
    else:
        assert run.await_count == 2
