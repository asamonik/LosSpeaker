"""Finish a prepared card's first boot without assuming a USB card index."""

import re
import subprocess
import time
from pathlib import Path

import httpx

pin = re.search(
    r"Your LosSpeaker admin PIN: (\d+)",
    Path("/var/log/losspeaker-install.log").read_text(),
).group(1)
with httpx.Client(
    base_url="http://127.0.0.1:8000", timeout=30, trust_env=False
) as client:
    for attempt in range(60):
        try:
            client.get("/api/v1/state").raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(1)
    else:
        raise RuntimeError("LosSpeaker did not start; inspect systemd service logs")
    client.post("/api/v1/admin/login", json={"pin": pin}).raise_for_status()
    devices = client.get("/api/v1/outputs").json()
    device = next((d for d in devices if "AB13X" in d["name"]), None)
    if device:
        # This adapter defaults to -20 dB in ALSA. Leave volume control to the
        # player instead of silently attenuating every source in hardware.
        card = re.fullmatch(r"hw:CARD=([\w-]+),DEV=\d+", device["id"]).group(1)
        subprocess.run(
            ["amixer", "-q", "-c", card, "sset", "PCM", "0dB", "unmute"],
            check=True,
        )
        subprocess.run(["alsactl", "store"], check=True)
        result = client.put(
            "/api/v1/outputs", json={"device": device["id"], "dop": False}
        )
        result.raise_for_status()
        print(
            "USB output configured:",
            device["name"],
            result.json()["player"]["output_format"],
        )
    else:
        print(
            "USB adapter not present; select an output in browser Settings when attached"
        )
    # The helper validates boot ordering before enabling the local mount.
    result = client.post(
        "/api/v1/storage",
        json={
            "name": "Local",
            "kind": "local",
            "remote": "/srv/losspeaker/local/Music",
        },
    )
    result.raise_for_status()
    print("Local library configured; mount dependency validation passed")
