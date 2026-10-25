#!/bin/bash
# Kinkaran: back up the shop's data (every bill and the price list).
#
#   scripts/backup.sh                 copy into $BACKUP_DIR (default ~/KinkaranBackups)
#   BACKUP_DIR=/Volumes/USB/kinkaran scripts/backup.sh
#
# Each run makes a dated folder with bills.json and inventory.json, checks the copies are valid
# JSON, and keeps the newest $KEEP folders (default 30). launchd runs it every night (see
# scripts/install_launchd.sh). For a copy that survives a dead or stolen Mac, point BACKUP_DIR at a
# USB drive or a synced folder, e.g. "$HOME/Library/Mobile Documents/com~apple~CloudDocs/Kinkaran".
#
# Restore: stop the server, copy both files from a backup folder into backend/, start again.
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-$HOME/KinkaranBackups}"
DATA_DIR="${DATA_DIR:-backend}"
KEEP="${KEEP:-30}"
PYTHON="${PYTHON:-.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3

# microseconds in the name: never collides, and names sort in the order the backups were made
stamp=$("$PYTHON" -c 'from datetime import datetime; print(datetime.now().strftime("%Y-%m-%d_%H%M%S_%f"))')
target="$BACKUP_DIR/kinkaran-$stamp"
mkdir -p "$target"

copied=0
for file in "$DATA_DIR/bills.json" "$DATA_DIR/inventory.json"; do
  if [ -f "$file" ]; then
    cp -p "$file" "$target/"  # the server replaces these files atomically, so a copy is never half-written
    "$PYTHON" -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$target/$(basename "$file")" \
      || { echo "❌ $file copy is not valid JSON" >&2; exit 1; }
    copied=$((copied + 1))
  fi
done
[ "$copied" -gt 0 ] || { echo "❌ nothing to back up (no bills.json or inventory.json in $DATA_DIR)" >&2; rmdir "$target"; exit 1; }

# keep only the newest $KEEP backups (newest names sort last; works with macOS and Linux tools)
ls -1d "$BACKUP_DIR"/kinkaran-* 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))" | while read -r old; do
  rm -rf "$old"
done

echo "✅ Backed up $copied file(s) to $target ($(ls -1d "$BACKUP_DIR"/kinkaran-* | wc -l | tr -d ' ') backups kept)"
