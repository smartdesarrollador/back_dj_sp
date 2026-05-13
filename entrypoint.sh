#!/bin/sh
set -e

# Fix media volume ownership at startup (named volume is owned by root on first mount)
mkdir -p /app/media
chown -R appuser:appuser /app/media

exec gosu appuser "$@"
