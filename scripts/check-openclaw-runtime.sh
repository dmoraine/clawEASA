#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${CLAW_EASA_VENV_DIR:-$ROOT/.venv}"
SKILL_DIR="${OPENCLAW_SKILL_DIR:-$HOME/.openclaw/workspace/skills/claw-easa}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[ -d "$ROOT" ] || fail "repo root missing: $ROOT"
[ -d "$VENV_DIR" ] || fail "virtualenv missing: $VENV_DIR"
[ -f "$SKILL_DIR/SKILL.md" ] || fail "skill not installed at: $SKILL_DIR"

# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"

python -m claw_easa.cli --help >/dev/null || fail "CLI help failed"
python -m claw_easa.cli status >/dev/null || fail "CLI status failed"

if [ -d "$ROOT/data" ] && [ -f "$ROOT/data/claw_easa.db" ]; then
  python -m claw_easa.cli sources-list >/dev/null || fail "sources-list failed with existing db"
fi

echo "OK: repo=$ROOT"
echo "OK: venv=$VENV_DIR"
echo "OK: skill=$SKILL_DIR"
echo "OK: claw-easa CLI responds"
