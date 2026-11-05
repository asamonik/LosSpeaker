# Pi deployment

Deployed on 2026-11-05 to `losspeaker.local` (Wi-Fi `192.168.0.183`, Ethernet `192.168.0.170` at verification).

## System

- Raspberry Pi 4B, 4 GB, 64-bit Raspberry Pi OS/Debian Trixie.
- SD card completely reflashed with official Raspberry Pi OS Lite ARM64 Trixie, release 2026-10-06. The extracted image and written card were checked against SHA-256 `b8a393dc1c3701450b5fffbfe74368a98fb6eab7415ce456cc0cead886ea8780` before expanding the filesystem.
- No moOde installation or old operating-system files retained. The existing Wi-Fi credentials, `pi` login and SSH host identity were preserved, and the owner's SSH public key was installed.
- Wi-Fi uses NetworkManager with a persistent profile, DHCP, Austrian regulatory domain and automatic connection at boot. Ethernet also uses DHCP.
- LosSpeaker runs as the dedicated `losspeaker` system user. nginx, NQPTP and Bluetooth are system services; the backend, MPD, AirPlay, Spotify and DSP are persistent user services.
- Prepared application assets and ARM64 receiver binaries were installed by `scripts/install-runtime.sh` on first boot; Node/npm and receiver build toolchains are unnecessary on the Pi. Running `scripts/install.sh` reinstalls build dependencies for upgrades.
- The `pi` SSH account's redundant desktop audio services and sockets are masked. Audio runs exclusively in the persistent `losspeaker` user session.

## Audio

Output is `hw:CARD=Audio,DEV=0`, an **AB13X USB Audio** adapter. Its kernel descriptors report stereo **S16_LE at 48 kHz only**. Processed playback automatically uses that format; this adapter does not provide high-resolution PCM or DSD.

Its ALSA PCM playback mixer is set to **0 dB, unmuted**; the adapter's initial setting was −20 dB and caused very quiet output. This is saved in `/var/lib/alsa/asound.state` and restored by the existing `alsa-restore.service`. Prepared-card setup now initializes this adapter at 0 dB. Browser/source volume controls and DSP headroom remain available.

The backend detects USB playback capabilities from `/proc/asound/<card>/stream0` and persists its selected output format across DSP edits. CamillaDSP owns the DAC in processed mode. Direct mode releases CamillaDSP and lets MPD open it exclusively.

A `Local` music source is preconfigured:

- Place files in `/srv/losspeaker/local/Music` (owned by `pi`).
- The player sees the read-only bind mount `/srv/losspeaker/music/Local`.
- Use **Settings → Music storage → Local → Scan** after adding music.
- Local and USB mounts do not depend on network startup. Generated mount units are checked for dependency cycles before activation.

## Verification

Completed checks:

- First-boot installation completed, followed by a successful reboot. Wi-Fi, Ethernet, the application and audio services returned automatically. No failed systemd units or boot dependency cycles were reported.
- Ethernet was temporarily disabled: SSH and the health API remained reachable over Wi-Fi, and internet traffic used `wlan0`. Ethernet management and its connection were restored after the check.
- All source adapters report ready in `/api/v1/health`; the production UI is served over nginx with demo mode disabled. CamillaDSP is configured for the connected USB adapter at stereo S16_LE/48 kHz.
- The live browser interface loads over Wi-Fi at desktop and mobile sizes without page errors or mobile horizontal overflow. Sources and output options load, and there are no simulated playback controls.
- The fullscreen visualizer is deployed at `/#visualizer` with Bloom, Warp and Terrain views, replacing the original line-based scenes. Live browser verification received 132 real spectrum frames from the Pi during Spotify playback, with working controls that hide after inactivity and return on tap. Desktop/mobile integration checks also cover theme persistence, transport, native fullscreen, closing, and the graphics fallback.
- The redesigned renderer includes fluid colour volumes, 28,000 tunnel particles, a 3D frequency-history surface, smoothed audio attacks/decays and bass-onset motion. Eight browser checks pass, including GPU rendering of all three views and an image-brightness check with time frozen that verifies real levels affect the scene and unavailable audio fades out. Resolution adapts to the viewing device; rendering stays outside the Pi's audio path.
- Bluetooth authorization and routing fixes are deployed: authorize A2DP profile UUID `110d`, keep paired-device reconnection enabled outside the pairing window, and route both PipeWire source and playback-stream forms to the Bluetooth bus. Transport activity is detected even when AVRCP reports stopped.
- Bluetooth stereo test playback ran for 60 seconds without disconnecting. The measured bus signals were 439/659 Hz for the 440/660 Hz test tones, with nonzero DSP playback levels and zero clipped samples. Stopping the stream retained the connection; disconnect/reconnect and playback succeeded with pairing and discoverability closed. The temporary test computer's pairing was removed afterwards.
- Advertised identity is LosSpeaker, Audio/Video Loudspeaker (`0x040414` with current registered service bits), with A2DP and AVRCP. Unneeded SIM Access/telephony and Bluetooth MIDI advertisements were removed. The backend suite passes 49 tests, including authorization, reconnection, source handoffs, routing/state regressions and capture freshness/lifecycle tests.
- Spotify's configured bitrate was raised from the implicit 160 kbps default to 320 kbps, and actual playback reports `OGG_VORBIS_320` at a 44.1 kHz source rate. The connected DAC still operates at its supported stereo S16_LE/48 kHz. Lossless FLAC is not enabled because the stock receiver lacks the required PlayPlay implementation.
- Visualizer latency was tested on the Pi using seven pulses in a private virtual sink. Against a separate fast capture monitor, median analysis response fell from 35.7 to 20.7 ms, and maximum response from 78.2 to 33.2 ms. Observed spectrum update rates increased from 12.2 to 30.4 Hz during the test. Browser-side changes additionally remove an up-to-85-ms texture upload hold, shorten attack smoothing and bypass React state updates for fullscreen audio frames. These measurements exclude physical speaker/display latency.
- A controlled browser step test measured the time from sending a spectrum update until the shader received 90% of the new energy. Four runs changed from 130/113/129/66 ms before the fix to 22/20/24/19 ms after it (medians 121/21 ms). The deployed live WebSocket delivered 50 frames at 29.7 Hz with a 33.2 ms median frame interval. The production build, 44 backend tests and eight desktop/mobile browser checks passed.
- Source handoff now consumes BlueZ playback-start signals directly instead of depending only on a once-per-second poll. Regression tests cover Bluetooth reclaiming playback from Spotify when a pause was missed, duplicate signals, idle remote metadata, and signal subscription cleanup. After deployment, the connected Pixel's Bluetooth track was observed as the active source with Spotify inactive; subsequent Spotify playback also took ownership. Phone pairing was retained.
- Visualizers now keep a floating song overlay visible while controls fade out. It displays title, artist and source-provided cover art, with a music icon if artwork is absent. Desktop/mobile checks verify persistence, live title/art changes, missing artwork and long-title layout. Initial HTTP state no longer replaces metadata already delivered by the live state socket.
- The preconfigured local music source is mounted. Administration credentials remain outside this repository.


- Bluetooth artwork lookup is deployed. Missing covers are looked up asynchronously in Apple’s iTunes catalogue from AVRCP title/artist metadata, checked against artist/title/version and cached locally. The Pi fetched the real cover for the phone’s reported “Sommerregen” by Chrissyjeey (JPEG, 7,484 bytes); a fresh cache instance reused it with no HTTP client, and nginx served it through `/api/v1/artwork/<track-hash>` with HTTP 200.
- Validation for this update: production frontend build, 64 backend tests, and 10 desktop/mobile browser checks pass. The deployed UI was checked using a browser-only Bluetooth state fixture and the real Pi-served cover: it renders at desktop/mobile sizes and remains visible after controls hide. Spotify was the actual active source during deployment verification and retained its own title/artwork; phone-to-Pi Bluetooth playback was not started for this check.

Before reflashing, silent WAV playback through both CamillaDSP and exclusive MPD was verified with the kernel confirming stereo S16_LE/48 kHz. Spotify playback is observed on the clean image and the owner confirmed audible output. Bluetooth stereo playback is now verified from the development computer through the DSP path; phone-specific playback should be confirmed by the owner. AirPlay client playback and a two-hour soak remain to be checked.

## Access

Open `http://losspeaker.local`. The generated administration PIN is recorded in the root-only installation log `/var/log/losspeaker-install.log`; it is not stored in this repository. SSH access remains through the existing `pi` account. See [operations](OPERATIONS.md) for PIN reset, service commands and upgrade instructions.

## Automatic artwork recovery

The player and fullscreen visualizer share an artwork component that retries failed image downloads automatically, beginning after one second and backing off to a maximum of 30 seconds. Local retries bypass cached failures, and switching images cancels pending retries for the previous cover. This fixes the reproduced case where an image failed once and stayed hidden until a page reload.

A regression test opens the visualizer without artwork, sends a late Bluetooth cover over the existing state connection, initially returns HTTP 503 for the image, then makes it available. Both the overlay and player show it without a document reload, at desktop and mobile sizes. A backend test also confirms a completed real lookup pipeline publishes its cover through an already-open state WebSocket.

Deployed as a frontend update without restarting the audio services. The production build and 65 backend tests pass; all 12 browser checks pass across the regression run and the focused rerun after correcting the navigation assertion to ignore hash changes. On the deployed UI, a browser-only fixture injected a late cover and an initial HTTP 503, then loaded the real cached Pi image successfully without a document reload or page errors. Actual Spotify playback and its own cover were also verified after deployment.
