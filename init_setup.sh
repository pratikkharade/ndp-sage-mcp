#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
STREAMING_DIR="$ROOT_DIR/streaming_v2"

echo "==> Creating virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

echo "==> Activating virtual environment..."
source "$VENV_DIR/bin/activate"

echo "==> Upgrading pip..."
python -m pip install --upgrade pip

echo "==> Installing project requirements..."
pip install -r "$ROOT_DIR/requirements.txt"

echo "==> Cloning streaming_v2..."
if [ ! -d "$STREAMING_DIR" ]; then
    git clone git@github.com:sci-ndp/streaming_v2.git "$STREAMING_DIR"
else
    echo "streaming_v2 already exists. Skipping clone."
fi

echo "==> Installing streaming_v2 in editable mode..."
pip install -e "$STREAMING_DIR"

echo
echo "========================================="
echo "Setup complete!"
echo
echo "To activate the environment later, run:"
echo
echo "source $VENV_DIR/bin/activate"
echo "========================================="