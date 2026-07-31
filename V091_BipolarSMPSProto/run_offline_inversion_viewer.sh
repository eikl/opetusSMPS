#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

uv run panel serve offline_inversion_viewer.py \
  --address 0.0.0.0 \
  --port 5006 \
  --allow-websocket-origin "*"
