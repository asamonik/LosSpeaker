#!/usr/bin/env bash
# Run on a dedicated Raspberry Pi OS Lite installation.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || { echo 'Requires a 64-bit Raspberry Pi (aarch64).' >&2; exit 1; }
source /etc/os-release
[[ ${VERSION_CODENAME:-} == trixie ]] || { echo 'Requires Raspberry Pi OS Lite Trixie.' >&2; exit 1; }
source_dir=$(cd "$(dirname "$0")/.." && pwd)
install_dir=/opt/losspeaker
state_dir=/var/lib/losspeaker
backup_dir=/var/backups/losspeaker
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 python3-venv python3-dev nodejs npm git rsync curl ca-certificates sudo nginx mpd alsa-utils pipewire pipewire-pulse pulseaudio-utils wireplumber libspa-0.2-bluetooth bluez avahi-daemon cifs-utils nfs-common build-essential autoconf automake libtool pkg-config libpopt-dev libconfig-dev libasound2-dev libpulse-dev libavahi-client-dev libssl-dev libsoxr-dev libglib2.0-dev libavcodec-dev libavformat-dev libavutil-dev libsodium-dev libgcrypt20-dev libplist-dev libdaemon-dev libmosquitto-dev xxd systemd-dev
id losspeaker &>/dev/null || useradd --system --create-home --home-dir /var/lib/losspeaker --shell /usr/sbin/nologin losspeaker
usermod -aG audio,bluetooth losspeaker
uid=$(id -u losspeaker)
loginctl enable-linger losspeaker
systemctl start "user@$uid.service"
userctl() { runuser -u losspeaker -- env XDG_RUNTIME_DIR="/run/user/$uid" systemctl --user "$@"; }
userctl stop losspeaker.service losspeaker-mpd.service losspeaker-airplay.service losspeaker-spotify.service losspeaker-dsp.service 2>/dev/null || true
install -d -m 700 "$backup_dir"
if [[ -f $state_dir/state.db ]]; then
 python3 - "$state_dir/state.db" "$backup_dir/state-$(date +%s).db" <<'PY'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as src,sqlite3.connect(sys.argv[2]) as dst:src.backup(dst)
PY
fi
install -d "$install_dir" /etc/losspeaker /usr/local/libexec /srv/losspeaker
if [[ $source_dir != "$install_dir" ]]; then
 rsync -a --exclude=.venv --exclude=node_modules --exclude=.git --exclude=.state "$source_dir/" "$install_dir/"
fi
python3 -m venv /opt/losspeaker-bootstrap
/opt/losspeaker-bootstrap/bin/pip install --disable-pip-version-check 'uv==0.13.5'
cd "$install_dir"
/opt/losspeaker-bootstrap/bin/uv sync --frozen --no-dev --python /usr/bin/python3
npm --prefix web ci
npm --prefix web run build
# Fixed release binaries, verified before extraction.
python3 - <<'PY'
import hashlib,io,tarfile,urllib.request
from pathlib import Path
items=[('https://github.com/HEnquist/camilladsp/releases/download/v4.1.3/camilladsp-linux-pulseaudio-aarch64.tar.gz','fb3b2917c4a9a5b9a518445d945256173f48ac8d516f744384dbf88f1466675b','camilladsp'),('https://github.com/devgianlu/go-librespot/releases/download/v0.10.5/go-librespot_linux_arm64.tar.gz','35274c1d3576090096d3459ecf76ee9c025258d19dc76f0bf724f95c525467e1','go-librespot')]
for url,digest,name in items:
 raw=urllib.request.urlopen(url,timeout=60).read()
 if hashlib.sha256(raw).hexdigest()!=digest:raise SystemExit('Checksum mismatch: '+name)
 with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
  entry=next(x for x in tar.getmembers() if Path(x.name).name==name and x.isfile())
  p=Path('/usr/local/bin')/name;p.write_bytes(tar.extractfile(entry).read());p.chmod(0o755)
PY
build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT
for component in nqptp shairport-sync; do
 if [[ $component == nqptp ]]; then revision=ab8fb67483411640fd7f1e193889482f2421c37c; else revision=0a34fbda83724cc06266aadcf7c88acc8fef4e7d; fi
 git clone -q "https://github.com/mikebrady/$component.git" "$build_dir/$component"
 cd "$build_dir/$component"
 git checkout -q "$revision"
 autoreconf -fi
 if [[ $component == nqptp ]]; then
  ./configure --with-systemd-startup
 else
  ./configure --sysconfdir=/etc --with-pulseaudio --with-soxr --with-avahi --with-ssl=openssl --with-airplay-2 --with-dbus-interface --with-metadata
 fi
 make -j2
 make install
 if [[ $component == shairport-sync ]]; then sed 's/shairport-sync/losspeaker/g' scripts/shairport-sync-dbus-policy.conf > /etc/dbus-1/system.d/losspeaker.conf; fi
done
cd "$install_dir"
getent group nqptp >/dev/null && usermod -aG nqptp losspeaker
install -d -o losspeaker -g losspeaker "$state_dir" "$state_dir/playlists" "$state_dir/filters" "$state_dir/.config" "$state_dir/.config/systemd/user" "$state_dir/.config/pipewire/pipewire-pulse.conf.d" "$state_dir/.config/wireplumber/wireplumber.conf.d" "$state_dir/spotify"
# Music mount targets are root-owned; the web process cannot replace them with symlinks.
install -d -o root -g root -m 755 "/srv/losspeaker/music"
[[ -f /etc/losspeaker/mpd.conf ]] || install -m 644 deploy/mpd.conf /etc/losspeaker/mpd.conf
install -m 644 deploy/spotify.yml "$state_dir/spotify/config.yml"
install -m 644 deploy/shairport-sync.conf /etc/losspeaker/shairport-sync.conf
install -m 644 deploy/pipewire.conf "$state_dir/.config/pipewire/pipewire-pulse.conf.d/50-losspeaker.conf"
install -m 644 deploy/wireplumber.conf "$state_dir/.config/wireplumber/wireplumber.conf.d/50-losspeaker.conf"
install -m 755 deploy/losspeaker-helper /usr/local/libexec/losspeaker-helper
printf '%s\n' 'losspeaker ALL=(root) NOPASSWD: /usr/local/libexec/losspeaker-helper' > /etc/sudoers.d/losspeaker
chmod 440 /etc/sudoers.d/losspeaker
visudo -cf /etc/sudoers.d/losspeaker
hosts="localhost,127.0.0.1,losspeaker.local,$(hostname),$(hostname).local"
for ip in $(hostname -I); do hosts="$hosts,$ip"; done
printf 'LOSSPEAKER_DATA=%s\nLOSSPEAKER_MUSIC=/srv/losspeaker/music\nLOSSPEAKER_HOSTS=%s\n' "$state_dir" "$hosts" > /etc/losspeaker/environment
for unit in losspeaker losspeaker-mpd losspeaker-airplay losspeaker-spotify losspeaker-dsp; do
 case $unit in
 losspeaker) command="$install_dir/.venv/bin/uvicorn losspeaker.main:app --host 127.0.0.1 --port 8000" ;;
 losspeaker-mpd) command="/usr/bin/mpd --no-daemon /etc/losspeaker/mpd.conf" ;;
 losspeaker-airplay) command="/usr/local/bin/shairport-sync -c /etc/losspeaker/shairport-sync.conf" ;;
 losspeaker-spotify) command="/usr/local/bin/go-librespot --config_dir $state_dir/spotify" ;;
 losspeaker-dsp) command="$install_dir/scripts/start-dsp.sh" ;;
 esac
 cat > "$state_dir/.config/systemd/user/$unit.service" <<UNIT
[Unit]
Description=$unit
After=pipewire-pulse.service
Wants=pipewire-pulse.service
[Service]
WorkingDirectory=$install_dir
EnvironmentFile=/etc/losspeaker/environment
Environment=HOME=$state_dir
ExecStart=$command
Restart=on-failure
RestartSec=3
UMask=0077
[Install]
WantedBy=default.target
UNIT
done
chown -R losspeaker:losspeaker "$state_dir/.config" "$state_dir/spotify"
# PIN is generated only on first installation and never written to source.
runuser -u losspeaker -- env LOSSPEAKER_DATA="$state_dir" LOSSPEAKER_MUSIC="/srv/losspeaker/music" "$install_dir/.venv/bin/python" - <<'PY'
import secrets
from losspeaker.core import Settings,Store
s=Settings();db=Store(s.data/'state.db')
if not db.get('pin'):
 pin=''.join(secrets.choice('0123456789') for _ in range(8));db.set_pin(pin);print('\nYour LosSpeaker admin PIN: '+pin+'\nSave this PIN now.\n')
PY
for legacy_unit in mpd.service mpd.socket shairport-sync.service; do
 systemctl disable --now "$legacy_unit" 2>/dev/null || true
done
install -m 644 deploy/bluetooth.conf /etc/bluetooth/main.conf
install -d /etc/systemd/system/bluetooth.service.d
install -m 644 deploy/bluetooth.service.conf /etc/systemd/system/bluetooth.service.d/losspeaker.conf
systemctl daemon-reload
systemctl enable --now bluetooth avahi-daemon nqptp
systemctl restart bluetooth
systemctl reload dbus
# The advertised name is independent of the system hostname.
cat > /etc/avahi/services/losspeaker.service <<'AVAHI'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group><name>LosSpeaker</name><service><type>_http._tcp</type><port>80</port></service></service-group>
AVAHI
install -m 644 deploy/nginx.conf /etc/nginx/sites-available/losspeaker
ln -sf /etc/nginx/sites-available/losspeaker /etc/nginx/sites-enabled/losspeaker
# Intended for a dedicated Pi; retain any default nginx site as a backup.
if [[ -L /etc/nginx/sites-enabled/default ]]; then mv /etc/nginx/sites-enabled/default "$backup_dir/nginx-default"; fi
nginx -t
systemctl enable --now nginx
systemctl reload nginx
userctl daemon-reload
userctl enable --now pipewire pipewire-pulse wireplumber
userctl restart pipewire pipewire-pulse wireplumber
userctl enable --now losspeaker-dsp losspeaker-mpd losspeaker-airplay losspeaker-spotify losspeaker
printf '\nInstalled. Open http://%s.local or http://%s . Select your DAC in Settings.\n' "$(hostname)" "$(hostname -I | awk '{print $1}')"
printf 'Reboot once to apply audio/Bluetooth group membership. See docs/OPERATIONS.md for verification.\n'
