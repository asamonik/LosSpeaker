#!/usr/bin/env bash
set -uo pipefail
printf 'LosSpeaker diagnostics (credentials excluded)\n'
uname -a
aplay -l
pactl list short sinks
pactl list short sources
systemctl --user --no-pager status losspeaker losspeaker-dsp losspeaker-mpd losspeaker-airplay losspeaker-spotify
systemctl --no-pager status nqptp bluetooth avahi-daemon
curl --fail --silent http://127.0.0.1:8000/api/v1/health
