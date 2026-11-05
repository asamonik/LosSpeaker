#!/usr/bin/env bash
set -euo pipefail
profile=${LOSSPEAKER_DATA:-/var/lib/losspeaker}/camilladsp.yml
if [[ -f $profile ]]; then exec /usr/local/bin/camilladsp -p 1234 "$profile"; fi
exec /usr/local/bin/camilladsp -p 1234 -w
