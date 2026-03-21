#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/skill/claw-easa/"
DST="${OPENCLAW_SKILL_DST:-$HOME/.openclaw/workspace/skills/claw-easa}"

if [ ! -d "$SRC" ]; then
  echo "Source skill directory not found: $SRC" >&2
  exit 1
fi

SRC_REAL="$(realpath "$SRC")"
DST_PARENT="$(dirname "$DST")"
mkdir -p "$DST_PARENT"

if [ -e "$DST" ] || [ -L "$DST" ]; then
  DST_REAL="$(realpath -m "$DST")"
  case "$DST_REAL" in
    "$ROOT"|"$ROOT"/*|"$SRC_REAL"|"$SRC_REAL"/*)
      echo "Refusing to install: destination resolves inside the source repository." >&2
      echo "  ROOT=$ROOT" >&2
      echo "  SRC=$SRC_REAL" >&2
      echo "  DST=$DST_REAL" >&2
      exit 1
      ;;
  esac
fi

if [ -L "$DST" ]; then
  LINK_TARGET="$(readlink -f "$DST" || true)"
  case "$LINK_TARGET" in
    "$ROOT"|"$ROOT"/*|"$SRC_REAL"|"$SRC_REAL"/*)
      echo "Refusing to install: destination symlink points back into the source repository." >&2
      echo "  DST=$DST -> $LINK_TARGET" >&2
      exit 1
      ;;
  esac
  rm "$DST"
elif [ -d "$DST" ]; then
  rm -rf "$DST"
elif [ -e "$DST" ]; then
  rm -f "$DST"
fi

mkdir -p "$DST"
rsync -a --delete "$SRC" "$DST/"
printf 'Installed claw-easa skill to %s\n' "$DST"
