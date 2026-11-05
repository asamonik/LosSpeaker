#!/usr/bin/env bash
# First-boot installation from a prepared ARM64 card with prebuilt artifacts.
set -euo pipefail
[[ $EUID == 0 && $(uname -m) == aarch64 ]] || { echo 'Requires root on an ARM64 Pi'; exit 1; }
umask 077
log_file=/var/log/losspeaker-install.log
touch "$log_file"
chmod 600 "$log_file"
exec >> "$log_file" 2>&1
printf '\nLosSpeaker first-boot setup: %s\n' "$(date -Is)"
export DEBIAN_FRONTEND=noninteractive
(cd /opt/losspeaker/prebuilt && sha256sum -c SHA256SUMS)
for attempt in $(seq 1 60); do
 if getent ahostsv4 deb.debian.org >/dev/null; then break; fi
 sleep 2
done
apt-get -o Acquire::Retries=3 update
mapfile -t runtime_libraries < /opt/losspeaker/prebuilt/runtime-packages.txt
apt-get -y --no-install-recommends -o Acquire::Retries=3 install python3 python3-venv sudo ca-certificates nginx mpd alsa-utils pipewire pipewire-pulse pulseaudio-utils wireplumber libspa-0.2-bluetooth bluez avahi-daemon avahi-utils libnss-mdns cifs-utils nfs-common dbus-user-session rfkill "${runtime_libraries[@]}"
for legacy_unit in mpd.socket mpd.service; do systemctl disable --now "$legacy_unit" 2>/dev/null || true; done
id losspeaker >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/losspeaker --shell /usr/sbin/nologin losspeaker
usermod -aG audio,bluetooth losspeaker
# The SSH account must not start a competing desktop audio graph at login.
# Only the dedicated losspeaker user runs PipeWire/WirePlumber on this image.
if id pi >/dev/null 2>&1; then
 pi_home=$(getent passwd pi | cut -d: -f6)
 install -d -o pi -g "$(id -gn pi)" "$pi_home/.config/systemd/user"
 for login_audio_unit in pipewire.service pipewire.socket pipewire-pulse.service pipewire-pulse.socket wireplumber.service filter-chain.service mpris-proxy.service; do
  ln -sfn /dev/null "$pi_home/.config/systemd/user/$login_audio_unit"
 done
fi
player_uid=$(id -u losspeaker)
state_dir=/var/lib/losspeaker
install -d /etc/losspeaker /usr/local/libexec /srv/losspeaker/music /srv/losspeaker/local
install -d -o pi -g pi -m 755 /srv/losspeaker/local/Music
install -d -o losspeaker -g losspeaker "$state_dir" "$state_dir/playlists" "$state_dir/filters" "$state_dir/spotify" "$state_dir/.config/systemd/user" "$state_dir/.config/pipewire/pipewire-pulse.conf.d" "$state_dir/.config/wireplumber/wireplumber.conf.d"
for binary in camilladsp go-librespot shairport-sync nqptp; do install -m 755 "/opt/losspeaker/prebuilt/$binary" "/usr/local/bin/$binary"; done
install -m 644 /opt/losspeaker/prebuilt/nqptp.service /etc/systemd/system/nqptp.service
install -m 644 /opt/losspeaker/prebuilt/shairport-dbus.conf /etc/dbus-1/system.d/losspeaker.conf
install -m 755 /opt/losspeaker/deploy/losspeaker-helper /usr/local/libexec/losspeaker-helper
[[ -f /etc/losspeaker/mpd.conf ]] || install -m 644 /opt/losspeaker/deploy/mpd.conf /etc/losspeaker/mpd.conf
install -m 644 /opt/losspeaker/deploy/shairport-sync.conf /etc/losspeaker/shairport-sync.conf
install -m 644 /opt/losspeaker/deploy/spotify.yml "$state_dir/spotify/config.yml"
install -m 644 /opt/losspeaker/deploy/pipewire.conf "$state_dir/.config/pipewire/pipewire-pulse.conf.d/50-losspeaker.conf"
install -m 644 /opt/losspeaker/deploy/wireplumber.conf "$state_dir/.config/wireplumber/wireplumber.conf.d/50-losspeaker.conf"
printf '%s\n' 'losspeaker ALL=(root) NOPASSWD: /usr/local/libexec/losspeaker-helper' > /etc/sudoers.d/losspeaker
chmod 440 /etc/sudoers.d/losspeaker
visudo -cf /etc/sudoers.d/losspeaker
hosts="localhost,127.0.0.1,losspeaker.local,$(hostname),$(hostname).local"
for address in $(hostname -I); do hosts="$hosts,$address"; done
printf 'LOSSPEAKER_DATA=%s\nLOSSPEAKER_MUSIC=/srv/losspeaker/music\nLOSSPEAKER_HOSTS=%s\n' "$state_dir" "$hosts" > /etc/losspeaker/environment
chmod 644 /etc/losspeaker/environment
for unit in losspeaker losspeaker-mpd losspeaker-airplay losspeaker-spotify losspeaker-dsp; do
 case $unit in
 losspeaker) service_command='/opt/losspeaker/.venv/bin/uvicorn losspeaker.main:app --host 127.0.0.1 --port 8000';;
 losspeaker-mpd) service_command='/usr/bin/mpd --no-daemon /etc/losspeaker/mpd.conf';;
 losspeaker-airplay) service_command='/usr/local/bin/shairport-sync -c /etc/losspeaker/shairport-sync.conf';;
 losspeaker-spotify) service_command='/usr/local/bin/go-librespot --config_dir /var/lib/losspeaker/spotify';;
 losspeaker-dsp) service_command='/opt/losspeaker/scripts/start-dsp.sh';;
 esac
 cat > "$state_dir/.config/systemd/user/$unit.service" <<UNIT
[Unit]
Description=$unit
Wants=pipewire-pulse.service
After=pipewire-pulse.service
[Service]
WorkingDirectory=/opt/losspeaker
EnvironmentFile=/etc/losspeaker/environment
Environment=HOME=/var/lib/losspeaker
ExecStart=$service_command
Restart=on-failure
RestartSec=3
UMask=0077
[Install]
WantedBy=default.target
UNIT
done
chown -R losspeaker:losspeaker "$state_dir/.config" "$state_dir/spotify"
runuser -u losspeaker -- env LOSSPEAKER_DATA="$state_dir" LOSSPEAKER_MUSIC=/srv/losspeaker/music /opt/losspeaker/.venv/bin/python - <<'PY'
import secrets
from losspeaker.core import Settings,Store
s=Settings();db=Store(s.data/'state.db')
if not db.get('pin'):
 pin=''.join(secrets.choice('0123456789') for _ in range(8));db.set_pin(pin);print('Your LosSpeaker admin PIN: '+pin)
PY
install -m 644 /opt/losspeaker/deploy/nginx.conf /etc/nginx/sites-available/losspeaker
ln -sf /etc/nginx/sites-available/losspeaker /etc/nginx/sites-enabled/losspeaker
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl reload dbus
install -m 644 /opt/losspeaker/deploy/bluetooth.conf /etc/bluetooth/main.conf
install -d /etc/systemd/system/bluetooth.service.d
install -m 644 /opt/losspeaker/deploy/bluetooth.service.conf /etc/systemd/system/bluetooth.service.d/losspeaker.conf
systemctl daemon-reload
rfkill unblock bluetooth
systemctl enable --now bluetooth avahi-daemon nqptp nginx
systemctl restart bluetooth
systemctl reload nginx
install -m 644 /dev/null /etc/avahi/services/losspeaker.service
cat > /etc/avahi/services/losspeaker.service <<'AVAHI'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group><name>LosSpeaker</name><service><type>_http._tcp</type><port>80</port></service></service-group>
AVAHI
chmod 644 /etc/avahi/services/losspeaker.service
loginctl enable-linger losspeaker
systemctl start "user@$player_uid.service"
userctl() { runuser -u losspeaker -- env XDG_RUNTIME_DIR="/run/user/$player_uid" systemctl --user "$@"; }
userctl daemon-reload
userctl mask --now filter-chain.service 2>/dev/null || true
userctl enable --now pipewire pipewire-pulse wireplumber
userctl restart pipewire pipewire-pulse wireplumber
userctl enable --now losspeaker-dsp losspeaker-mpd losspeaker-airplay losspeaker-spotify losspeaker
/opt/losspeaker/.venv/bin/python /opt/losspeaker/scripts/configure-first-output.py
printf '\nLOSSPEAKER_SETUP_COMPLETE %s\n' "$(date -Is)"
touch /var/lib/losspeaker/setup-complete
apt-get clean
