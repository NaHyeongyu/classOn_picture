#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${VENV_DIR:-.venv}

echo "[setup] Using python: $(command -v "$PYTHON_BIN" || echo not-found)"

if [ ! -d "$VENV_DIR" ]; then
  echo "[setup] Creating venv at $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip

echo "[setup] Installing requirements.txt"
set +e
pip install -r requirements.txt
STATUS=$?
set -e

if [ $STATUS -ne 0 ]; then
  uname_s=$(uname -s || echo unknown)
  uname_m=$(uname -m || echo unknown)
  if [ "$uname_s" = "Darwin" ] && [ "$uname_m" = "arm64" ]; then
    echo "[setup] Detected macOS arm64. Retrying with onnxruntime-silicon..."
    pip uninstall -y onnxruntime || true
    pip install onnxruntime-silicon
    # install rest without onnxruntime
    tmp_req=$(mktemp)
    grep -v '^onnxruntime' requirements.txt > "$tmp_req"
    pip install -r "$tmp_req"
    rm -f "$tmp_req"
  else
    echo "[setup] requirements install failed (status=$STATUS)." >&2
    exit $STATUS
  fi
fi

echo "[setup] Done. Activate with: source $VENV_DIR/bin/activate"

