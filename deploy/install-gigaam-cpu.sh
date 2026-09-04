#!/usr/bin/env bash
# Install / repair GigaAM venv with CPU-only PyTorch (no CUDA wheels).
set -euo pipefail
ROOT="${1:-/opt/food_checking}"
VENV="$ROOT/gigaam-venv"
SRC="$ROOT/gigaam-src"

if [[ ! -d "$SRC" ]]; then
  git clone --depth 1 https://github.com/salute-developers/GigaAM.git "$SRC"
fi
if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install -U pip wheel
"$VENV/bin/pip" install -r "$ROOT/gigaam/requirements.txt"
"$VENV/bin/pip" install -e "$SRC"

# Force CPU wheels (gigaam[torch] may pull CUDA builds on Linux).
"$VENV/bin/pip" uninstall -y torch torchaudio triton 2>/dev/null || true
"$VENV/bin/pip" freeze | grep -iE '^nvidia-|^cuda-' | cut -d= -f1 | xargs -r "$VENV/bin/pip" uninstall -y
"$VENV/bin/pip" install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

"$VENV/bin/python" - <<'PY'
import torch
assert not torch.cuda.is_available(), "expected CPU-only torch"
print("ok", torch.__version__)
PY
