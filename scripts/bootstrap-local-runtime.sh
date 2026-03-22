#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${CLAW_EASA_VENV_DIR:-$ROOT/.venv}"
PYTHON_BIN="${CLAW_EASA_PYTHON_BIN:-python3}"
INSTALL_MODE="${CLAW_EASA_INSTALL_MODE:-editable}"
TORCH_VARIANT="${CLAW_EASA_TORCH_VARIANT:-cpu}"
TORCH_INDEX_URL="${CLAW_EASA_TORCH_INDEX_URL:-https://download.pytorch.org/whl/cpu}"
PIP_COMMON_ARGS="${CLAW_EASA_PIP_ARGS:---no-cache-dir}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python not found: $PYTHON_BIN" >&2
  exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtualenv at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"

# shellcheck disable=SC2086
python -m pip install $PIP_COMMON_ARGS --upgrade pip setuptools wheel >/dev/null

if ! python - <<'PY' >/dev/null 2>&1
import importlib.util
raise SystemExit(0 if importlib.util.find_spec('torch') else 1)
PY
then
  case "$TORCH_VARIANT" in
    cpu)
      echo "Installing CPU-only torch from $TORCH_INDEX_URL"
      # shellcheck disable=SC2086
      python -m pip install $PIP_COMMON_ARGS torch==2.10.0+cpu --index-url "$TORCH_INDEX_URL"
      ;;
    skip)
      echo "Skipping explicit torch preinstall"
      ;;
    *)
      echo "Unsupported CLAW_EASA_TORCH_VARIANT: $TORCH_VARIANT" >&2
      echo "Use: cpu | skip" >&2
      exit 1
      ;;
  esac
fi

case "$INSTALL_MODE" in
  editable)
    # shellcheck disable=SC2086
    python -m pip install $PIP_COMMON_ARGS -e "$ROOT"
    ;;
  wheel)
    # shellcheck disable=SC2086
    python -m pip install $PIP_COMMON_ARGS build >/dev/null
    python -m build --wheel "$ROOT" >/dev/null
    # shellcheck disable=SC2086
    python -m pip install $PIP_COMMON_ARGS --force-reinstall "$ROOT"/dist/*.whl
    ;;
  *)
    echo "Unsupported CLAW_EASA_INSTALL_MODE: $INSTALL_MODE" >&2
    echo "Use: editable | wheel" >&2
    exit 1
    ;;
esac

python -m claw_easa.cli status

echo
echo "Runtime ready."
echo "Activate with: . '$VENV_DIR/bin/activate'"
echo "CLI check: python -m claw_easa.cli --help"
echo "Torch variant: $TORCH_VARIANT"
