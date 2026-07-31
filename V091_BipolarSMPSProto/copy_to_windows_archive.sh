#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

REMOTE_HOST="${REMOTE_HOST:-mittaaja@100.74.217.78}"
REMOTE_DIR_WIN="${REMOTE_DIR_WIN:-C:/Users/mittaaja/Documents/V09_BipolarProto}"
ARCHIVE_NAME="V09_BipolarProto_sync.tar.gz"
ARCHIVE_PATH="/tmp/${ARCHIVE_NAME}"

tar \
  --exclude='./__pycache__' \
  --exclude='./*/__pycache__' \
  --exclude='./.pytest_cache' \
  --exclude='./.ruff_cache' \
  --exclude='./.venv' \
  --exclude='./venv' \
  --exclude='./logs' \
  --exclude='./saved_inversions' \
  --exclude='./settings_inversion.json' \
  -czf "${ARCHIVE_PATH}" .

scp "${ARCHIVE_PATH}" "${REMOTE_HOST}:${ARCHIVE_NAME}"

ssh -T "${REMOTE_HOST}" \
  "powershell.exe -NoProfile -Command \"New-Item -ItemType Directory -Force -Path '${REMOTE_DIR_WIN}'; tar -xzf '${ARCHIVE_NAME}' -C '${REMOTE_DIR_WIN}'; Remove-Item '${ARCHIVE_NAME}'\""

rm -f "${ARCHIVE_PATH}"
