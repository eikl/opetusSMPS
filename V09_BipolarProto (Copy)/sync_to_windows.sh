#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

REMOTE_HOST="${REMOTE_HOST:-mittaaja@100.74.217.78}"
RSYNC_PATH="${RSYNC_PATH:-rsync}"
SSH_CMD="${SSH_CMD:-ssh -T}"

# cwRsync/Cygwin style path. If using MSYS2 rsync on Windows, run with:
# REMOTE_DIR=/c/Users/mittaaja/Documents/V09_BipolarProto ./sync_to_windows.sh
REMOTE_DIR="${REMOTE_DIR:-/cygdrive/c/Users/mittaaja/Documents/V09_BipolarProto}"

rsync -avz --progress --blocking-io \
  -e "${SSH_CMD}" \
  --rsync-path "${RSYNC_PATH}" \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude 'logs/' \
  --exclude 'saved_inversions/' \
  --exclude 'settings_inversion.json' \
  ${RSYNC_EXTRA:-} \
  ./ "${REMOTE_HOST}:${REMOTE_DIR}/"
