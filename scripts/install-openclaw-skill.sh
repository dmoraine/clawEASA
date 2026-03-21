#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/skill/claw-easa/"
DST="$HOME/.openclaw/workspace/skills/claw-easa"

mkdir -p "$(dirname "$DST")"
if [ -L "$DST" ]; then
  rm "$DST"
elif [ -d "$DST" ]; then
  rm -rf "$DST"
fi
mkdir -p "$DST"
rsync -a --delete "$SRC" "$DST/"
printf 'Installed claw-easa skill to %s\n' "$DST"
