import runpy
import shutil
import subprocess
from pathlib import Path
import pytest

helper = runpy.run_path(str(Path(__file__).parents[2] / "deploy/losspeaker-helper"))


@pytest.mark.parametrize(
    "kind,filesystem,options",
    [("local", "none", "bind,ro"), ("usb", "auto", "ro,nosuid,nodev,noexec")],
)
def test_local_storage_does_not_introduce_boot_ordering_cycle(
    tmp_path, kind, filesystem, options
):
    definition = helper["mount_definition"](
        "Local",
        kind,
        "/srv/losspeaker/local/Music",
        "/srv/losspeaker/music/Local",
        filesystem,
        options,
    )
    assert "network-online.target" not in definition
    if not shutil.which("systemd-analyze"):
        pytest.skip("systemd analyzer is unavailable on this development machine")
    unit = tmp_path / "srv-losspeaker-music-Local.mount"
    unit.write_text(definition)
    result = subprocess.run(
        ["systemd-analyze", "verify", str(unit)], capture_output=True, text=True
    )
    assert "Found ordering cycle:" not in result.stderr, result.stderr


@pytest.mark.parametrize("kind,filesystem", [("smb", "cifs"), ("nfs", "nfs")])
def test_network_mounts_retain_network_dependency(kind, filesystem):
    definition = helper["mount_definition"](
        "NAS", kind, "server:/music", "/srv/losspeaker/music/NAS", filesystem, "ro"
    )
    assert "After=network-online.target" in definition
