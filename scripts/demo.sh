#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen
npm --prefix web ci
npm --prefix web run build
export LOSSPEAKER_DEMO=1
exec uv run --frozen uvicorn losspeaker.main:app --host 127.0.0.1 --port "${PORT:-8000}"
