#!/usr/bin/env bash
# Sync content between a Mac and the AI1 content pipeline.
# Requires the deploy key at .deploy-keys/ai1_deploy next to this script's parent.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY="$ROOT/.deploy-keys/ai1_deploy"
REMOTE_USER="cyrusminer"
REMOTE_HOST="100.123.141.71"
REMOTE_ROOT="/home/cyrusminer/content-system"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=10)
RSYNC_SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=10"

usage() {
  cat <<'EOF'
Usage:
  ./sync-content-ai1.sh push <video-or-folder>
  ./sync-content-ai1.sh pull
  ./sync-content-ai1.sh open
  ./sync-content-ai1.sh status

Examples:
  ./sync-content-ai1.sh push ~/Movies/launch-video.mp4
  ./sync-content-ai1.sh push ~/Movies/this-week/
  ./sync-content-ai1.sh pull
EOF
}

mkdir -p "$ROOT/content-system/01_RAW" "$ROOT/content-system/02_READY" "$ROOT/content-system/03_POSTIZ_UPLOAD" "$ROOT/content-system/04_PUBLISHED"

case "${1:-}" in
  push)
    SOURCE="${2:?Provide a video or folder to push.}"
    [ -e "$SOURCE" ] || { printf 'Not found: %s\n' "$SOURCE" >&2; exit 1; }
    if [ -d "$SOURCE" ]; then
      rsync -avz --progress -e "$RSYNC_SSH" "${SOURCE%/}/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_ROOT/01_RAW/"
    else
      rsync -avz --progress -e "$RSYNC_SSH" "$SOURCE" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_ROOT/01_RAW/"
    fi
    printf '\nPushed. AI-1 will pick up new videos automatically.\n'
    ;;
  pull)
    rsync -avz --progress -e "$RSYNC_SSH" \
      --include='*/' \
      --include='*.mp4' \
      --include='*.html' \
      --include='*.json' \
      --exclude='_ve_work/' \
      --exclude='*' \
      "$REMOTE_USER@$REMOTE_HOST:$REMOTE_ROOT/02_READY/" "$ROOT/content-system/02_READY/"
    rsync -avz --progress -e "$RSYNC_SSH" \
      --include='*/' \
      --include='*.mp4' \
      --include='*.html' \
      --include='*.json' \
      --exclude='_ve_work/' \
      --exclude='*' \
      "$REMOTE_USER@$REMOTE_HOST:$REMOTE_ROOT/03_POSTIZ_UPLOAD/" "$ROOT/content-system/03_POSTIZ_UPLOAD/"
    printf '\nFinished content is in:\n  %s\n  %s\n' "$ROOT/content-system/02_READY" "$ROOT/content-system/03_POSTIZ_UPLOAD"
    ;;
  open)
    open "https://postiz.nimaataie.com/"
    ;;
  status)
    "${SSH[@]}" "$REMOTE_USER@$REMOTE_HOST" 'printf "AI pipeline: "; systemctl --user is-active content-editor.service; printf "Tailscale IP: "; tailscale ip -4 | head -n 1; cd ~/postiz && docker compose ps --format "table {{.Name}}\t{{.Status}}"'
    ;;
  *)
    usage
    exit 1
    ;;
esac
