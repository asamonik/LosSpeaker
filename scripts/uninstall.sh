#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.'; exit 1; }
uid=$(id -u losspeaker)
userctl() { runuser -u losspeaker -- env XDG_RUNTIME_DIR="/run/user/$uid" systemctl --user "$@"; }
userctl disable --now losspeaker losspeaker-dsp losspeaker-mpd losspeaker-airplay losspeaker-spotify
rm -f /var/lib/losspeaker/.config/systemd/user/losspeaker*.service
rm -f /var/lib/losspeaker/.config/pipewire/pipewire-pulse.conf.d/50-losspeaker.conf /var/lib/losspeaker/.config/wireplumber/wireplumber.conf.d/50-losspeaker.conf
rm -f /etc/nginx/sites-enabled/losspeaker /etc/nginx/sites-available/losspeaker /etc/avahi/services/losspeaker.service /etc/sudoers.d/losspeaker /usr/local/libexec/losspeaker-helper
userctl daemon-reload
userctl restart pipewire pipewire-pulse wireplumber
systemctl reload nginx
printf 'Application services removed. Music, settings, credentials, backups and shared audio packages are retained. See OPERATIONS.md for mount cleanup.\n'
