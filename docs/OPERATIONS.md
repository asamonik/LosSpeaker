# Operations and hardware acceptance

## Services and state

The `losspeaker` system user owns five user services: `losspeaker` (API/UI), `losspeaker-dsp`, `losspeaker-mpd`, `losspeaker-airplay`, and `losspeaker-spotify`. PipeWire, pipewire-pulse and WirePlumber run in the same persistent user session. NQPTP, BlueZ, Avahi and nginx are system services.

Application state is in `/var/lib/losspeaker`. Music mount targets are in root-owned `/srv/losspeaker/music`; configuration is in `/etc/losspeaker`. The CamillaDSP active and previous profiles are retained in the state directory. FIR uploads are limited to mono PCM WAVs, 16 MB each. Profile YAML is limited to 1 MB and cannot select arbitrary capture/playback backends or external coefficient paths.

Run user-service commands in the correct user session:

```sh
sudo -u losspeaker env XDG_RUNTIME_DIR=/run/user/$(id -u losspeaker) systemctl --user status losspeaker
sudo -u losspeaker env XDG_RUNTIME_DIR=/run/user/$(id -u losspeaker) journalctl --user -u losspeaker -n 100
sudo -u losspeaker env XDG_RUNTIME_DIR=/run/user/$(id -u losspeaker) /opt/losspeaker/scripts/diagnose.sh
```

No audio services are installed on the development computer by running the test suite or demo.

## Storage

Unlock Settings and add a source with an alphanumeric name:

- **SMB:** `//nas/Music`, with optional username and password. SMB3 and read-only access are used. Credentials are root-only under `/etc/losspeaker/credentials`.
- **NFS:** `nas:/exports/music`. Read-only mounts use bounded soft timeouts to avoid indefinitely stalled UI requests.
- **USB:** filesystem UUID from `lsblk -f`, such as `1234-ABCD`. Disks are never formatted. USB mounts use UUID rather than changing device names.
- **Local:** create a folder under `/srv/losspeaker/local`, place your music there and grant the `losspeaker` user read/traverse access. Enter its absolute path. The source is bind-mounted read-only.

Select **Scan** after mounting or reconnecting a source. Scans target one available, nonempty subtree; offline/empty sources are not scanned, preventing accidental deletion of their index. After initially adding sources, reload Library to see the indexed folders. NAS connectivity and USB reconnects can require restarting the generated mount unit; automatic hotplug rescanning is not implemented.

To remove a mount, identify its exact unit with `systemd-escape --path --suffix=mount /srv/losspeaker/music/NAME`, stop/disable that unit and remove its unit file from `/etc/systemd/system`. Music is retained. Removing storage entries through the browser is not implemented yet.

## Audio and correction profiles

Select a DAC before starting playback. CamillaDSP initially waits without opening hardware. Unsupported rates, channel maps or sample formats cause an error rather than an automatic route to unprocessed audio.

Apply a stereo EQ profile through Audio. For room correction/crossovers, upload referenced WAVs, then import a CamillaDSP YAML configuration. Its capture is fixed to the stereo bus and its playback to the selected DAC; filters, mixer, pipeline and output channel count are retained. Output device changes require resetting an imported profile first. Imported profiles always require DSP, even if their channel count is stereo.

DSP changes disconnect the audible source while validating/applying the configuration, then reconnect after startup. A failed apply restores the saved profile where possible and leaves output disconnected for recovery. These software checks do not replace testing physical output assignments before connecting an active crossover to drivers.

Direct playback stops MPD while changing hardware ownership. Start the chosen file again after changing modes. The browser does not attenuate native DSD. Native DSD requires MPD/kernel/DAC support; DoP is a separate explicit output setting. Return to processed mode before changing the DAC or DSP. A wireless playback start restores the processed path automatically.

For USB audio devices, playback PCM formats/rates are read from the kernel USB descriptors. Processed mode selects a supported PCM format, including 16-bit-only adapters. Non-USB outputs default to 32-bit PCM. The audio engine validates the configured rate when it opens the device. The spectrum is pre-DSP; output meters use CamillaDSP telemetry when available, with labelled input-meter fallback.

If playback is unexpectedly quiet, inspect the selected card's hardware mixer as well as browser and sender volume. The AB13X adapter defaults to −20 dB in ALSA; prepared-card setup sets its PCM playback control to 0 dB and unmutes it. `sudo alsactl store` saves hardware levels for the existing `alsa-restore.service` to restore at boot. The default −6 dB DSP preamp provides EQ headroom separately.

Spotify requests 320 kbps Vorbis explicitly (`bitrate: 320` in `/var/lib/losspeaker/spotify/config.yml`); go-librespot otherwise defaults to 160. The live format appears in the player's audio details. FLAC is not enabled: the upstream stock build lacks its required PlayPlay implementation, so setting `flac_enabled` alone does not provide working lossless playback. See the pinned [configuration schema](https://github.com/devgianlu/go-librespot/blob/v0.10.5/config_schema.json) and [PlayPlay implementation](https://github.com/devgianlu/go-librespot/blob/v0.10.5/playplay/impl/impl.go). Normalization remains at the standard setting without additional pregain; EQ/headroom and output format are independent of source bitrate.

On the prepared appliance image, the `pi` SSH account's desktop audio services/sockets are masked. The dedicated `losspeaker` user owns the audio graph; SSH logins should not create a second PipeWire/WirePlumber session that competes for resources or Bluetooth devices.

Visualization uses a dedicated, continuously drained PCM monitor with a requested 512-frame capture latency and a rolling 2048-frame FFT window (42.7 ms at 48 kHz). It publishes the latest window at up to 30 Hz and drops stale subscriber frames. Fullscreen rendering reads the current frame directly each animation tick; live GPU uploads do not wait for history scrolling or React updates. Visual attack smoothing is approximately 7 ms, with a separate 63 ms decay. These are software timings, not a guarantee of total acoustic-to-screen delay on every browser or audio device.

## Access and recovery

The player is designed for a trusted home LAN. Playback and radio favourites are accessible without login; configuration uses a PIN-protected, eight-hour HttpOnly session. Cross-origin writes/WebSockets are rejected and login failures are throttled. This is not a public-facing service. HTTP is intended for the home LAN; TLS/remote access is outside this release.

The installer records allowed hostnames/IPs in `/etc/losspeaker/environment`. If DHCP changes the Pi's IP, update `LOSSPEAKER_HOSTS` and restart the backend; hostname access is unaffected. Bluetooth discoverability is open for two minutes after an administrator requests it. The headless agent rejects new pairing outside that window and supports A2DP reception, not Bluetooth speaker output. Legacy PIN/passkey pairing is unsupported.

Bluetooth advertises the loudspeaker device class with A2DP reception and AVRCP controls. HFP/HSP, SIM Access and Bluetooth MIDI are disabled. The adapter remains connectable for paired senders when discovery and pairing close. Phones may retain an old device icon until they refresh their cached device information.

Incoming A2DP authorization must allow profile UUID `110d` as well as the Audio Source/Sink and AVRCP UUIDs: [BlueZ requests authorization for the A2DP profile](https://github.com/bluez/bluez/blob/5.82/profiles/audio/a2dp.c). Pending/active transport state takes precedence over absent or stale AVRCP metadata. PipeWire can expose received audio as either a source node or a playback stream; both are routed into `losspeaker.bluetooth`, then only the selected source reaches the DSP bus. The backend repairs an incoming stream attached to the wrong sink.

Bluetooth handoffs also listen directly for BlueZ transport and remote-player signals. A new stream or play/resume event can take ownership from Spotify even if a short pause occurred between polls, or Bluetooth's previous activity flag remained set. The handoff unlinks the old route, pauses Spotify and links Bluetooth. Repeated status/metadata does not reclaim playback, and a connected phone's remote Spotify metadata cannot take ownership without an active/pending A2DP transport. Polling remains a fallback, with Bluetooth observations serialized against signal-driven updates.

Bluetooth artwork uses a background lookup in Apple’s iTunes Search API when the phone supplies a title and artist. The first lookup sends those fields to Apple; album metadata helps choose among results locally. Results must match the artist, title and mix/version before use. Images are downloaded only from Apple’s artwork CDN, limited to 512 KiB, and served by the Pi at `/api/v1/artwork/<track-hash>`. Searches are spaced at least 3.1 seconds apart, independently of playback and source handoffs.

Up to 256 artwork lookup entries are retained under `/var/lib/losspeaker/artwork`; positive matches persist across restarts and work offline. Unmatched tracks retry after 12 hours, temporary network/image failures after five minutes. A track change cancels a pending lookup, and late results cannot replace another track’s cover. Missing metadata or artwork leaves the placeholder without changing playback status. Completed lookups publish immediately to already-open browser sessions. If an image download fails, the player and fullscreen visualizer retry automatically with increasing delays (one second initially, up to 30 seconds); changing covers cancels the old retries. Local retries bypass failed browser cache entries. To clear this optional cache, stop the backend, remove only `/var/lib/losspeaker/artwork`, and start the backend again; it recreates the directory automatically.

Recover a lost admin PIN from an SSH session on the Pi (stop the backend first):

```sh
sudo -u losspeaker env XDG_RUNTIME_DIR=/run/user/$(id -u losspeaker) systemctl --user stop losspeaker
sudo -u losspeaker env LOSSPEAKER_DATA=/var/lib/losspeaker LOSSPEAKER_MUSIC=/srv/losspeaker/music /opt/losspeaker/.venv/bin/python -c 'from getpass import getpass; from losspeaker.core import Settings,Store; s=Settings(); Store(s.data/"state.db").set_pin(getpass("New PIN: "))'
sudo -u losspeaker env XDG_RUNTIME_DIR=/run/user/$(id -u losspeaker) systemctl --user start losspeaker
```

## Updates and uninstall

Copy the new repository onto the Pi and rerun `sudo ./scripts/install.sh`. The installer stops application services, makes a consistent SQLite backup under `/var/backups/losspeaker`, refreshes dependencies/builds and restarts services. Dependencies are locked in `uv.lock` and `web/package-lock.json`; upstream audio versions/checksums are pinned in the installer. A full appliance rollback requires retaining the previous source checkout and state directory as well as the SQLite backup.

Run `sudo ./scripts/uninstall.sh` to remove application services and integration configuration. It retains music, database, profiles, credentials, mount units and shared packages. Review/remove music mount units as above. Restore nginx's previous default symlink from `/var/backups/losspeaker/nginx-default` if needed. Shairport Sync/NQPTP binaries installed from source are retained; remove them separately only after confirming no other services use them.

## Hardware acceptance checklist

These steps remain required on physical Pi hardware; local demo/browser tests do not establish audio compatibility.

1. Install on fresh Trixie Lite ARM64 on Pi 4 and Pi 5. Reboot without an interactive user login; verify all services and a selectable USB DAC.
2. Confirm AirPlay discovery/playback from a current Apple device, Spotify Connect with Premium, and Bluetooth audio from Android/iOS. Verify artist/title metadata and capability-dependent controls. Check Bluetooth pairing closes after 120 seconds and remembered-device reconnects still work.
3. Start each pair of sources in quick succession. Verify only the most recent is audible, repeated status does not steal playback, and a paused/disconnected old source never automatically resumes.
4. Restart backend/receiver services during playback, unplug/replug the DAC and interrupt Wi-Fi. Confirm errors appear and recovery never connects an unprocessed bypass route.
5. Test tagged FLAC/MP3/WAV/DSF files and artwork, queues, playlists, local/USB/SMB/NFS browsing. Remove a USB drive or disconnect NAS during playback; confirm a later offline scan retains indexed entries.
6. Disconnect internet access; cached radio searches and saved stream URLs remain available. Test a dead stream and an unavailable Radio Browser mirror.
7. Use generated sweeps/impulses and loopback capture to verify graphic/parametric responses and channel maps. Test FIR import, invalid/missing coefficients, mismatched rates and rollback after a device-start failure.
8. Verify 24-bit 96/192 kHz PCM and native/DoP DSD separately on capable hardware. Confirm direct/DSP ownership transitions and fixed-volume indication. Test a multichannel interface with an explicit imported mixer.
9. Run continuous stereo playback for at least two hours at 48 kHz. Review CamillaDSP logs, clipping, CPU load and underruns. Repeat while multiple browser clients open/close visualization; slow clients must not affect audio.
10. Exercise Safari on iPhone and desktop/mobile Chromium, including browser sleep/reconnect. Test PIN protection, login throttling and configuration persistence after reboot.

No microphone calibration, filter generation, visual crossover designer, synchronized multiroom, Wi-Fi provisioning, flashable image or remote access is included.
