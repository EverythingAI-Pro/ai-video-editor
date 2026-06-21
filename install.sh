#!/usr/bin/env bash
# install.sh — one command to get the AI Video Editor ready.
#   bash install.sh
# Checks ffmpeg, creates the Python env, and tells you the exact next step.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
echo "🎬  AI Video Editor — installer"
echo

# ffmpeg
if command -v ffmpeg >/dev/null 2>&1; then
  echo "✓ ffmpeg found"
else
  echo "✗ ffmpeg not found — this is required."
  echo "    macOS:  brew install ffmpeg"
  echo "    Ubuntu: sudo apt install ffmpeg"
  echo "  Install it, then re-run: bash install.sh"
  exit 1
fi

# yt-dlp (optional, for web B-roll)
if command -v yt-dlp >/dev/null 2>&1; then
  echo "✓ yt-dlp found (web B-roll sourcing enabled)"
else
  echo "• yt-dlp not found (optional — only needed to source B-roll from URLs)."
  echo "    macOS: brew install yt-dlp   ·   pip: pipx install yt-dlp"
fi

# Python env
echo
echo "Setting up the Python environment…"
bash scripts/setup.sh

# .env
if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "• Created .env — paste your ELEVENLABS_API_KEY for best transcription (optional)."
fi

cat <<'NEXT'

✅ Done. Edit your first video:

   .venv/bin/python scripts/edit.py /path/to/your-video.mp4

Then open the review.html it creates, copy the captions, and post.
Customize your style anytime in taste.yaml.
NEXT
