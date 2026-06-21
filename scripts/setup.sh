#!/usr/bin/env bash
# setup.sh — one-time, self-contained setup for the video-editor skill.
# Creates a local .venv with the few Python deps the skill needs (Pillow, numpy, requests).
# A founder never runs this by hand — FounderOS runs it the first time the skill is used.
# Safe to re-run; it only does work that's missing.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

echo "video-editor setup: $SKILL_DIR"

# 1) ffmpeg / ffprobe must exist (the editor shells out to them).
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "  ! ffmpeg not found."
  echo "    macOS:  brew install ffmpeg"
  echo "    (yt-dlp is optional, only needed for sourcing b-roll from URLs: brew install yt-dlp)"
fi

# 2) Python venv.
PY="${PYTHON:-python3}"
if [ ! -x "$VENV/bin/python" ]; then
  echo "  creating venv …"
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
echo "  installing python deps (Pillow, numpy, requests) …"
"$VENV/bin/python" -m pip install --quiet pillow numpy requests || {
  echo "  ! pip install failed — check network/proxy."
  exit 1
}

# 3) Sanity check.
"$VENV/bin/python" - <<'PYEOF'
import PIL, numpy, requests  # noqa
import sys
print(f"  ok: Pillow {PIL.__version__}, numpy {numpy.__version__}, python {sys.version.split()[0]}")
PYEOF

echo "video-editor setup complete. The editor will use $VENV/bin/python."
