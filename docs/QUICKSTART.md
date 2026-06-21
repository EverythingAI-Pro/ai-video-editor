# Quick Start

## 1. Install (once)

```bash
git clone https://github.com/EverythingAI-Pro/ai-video-editor.git
cd ai-video-editor
bash install.sh
```

`install.sh` checks `ffmpeg`, builds a local Python env, and creates `.env`. Paste your
`ELEVENLABS_API_KEY` into `.env` for best transcription (free tier works; optional).

## 2. Edit a video (everything)

```bash
.venv/bin/python scripts/edit.py /path/to/your-video.mp4
```

This cuts filler, adds B-roll, burns captions, color grades, makes Shorts, writes per-platform
captions, and builds `review.html`. Open `review.html` and you're ready to post.

## 3. Do one thing at a time (optional)

Each step is its own script if you want control:

```bash
PY=.venv/bin/python

# transcribe (word-level)
$PY scripts/ve_transcribe.py video.mp4 --edit-dir edit

# plan the cut (read it before rendering)
$PY scripts/ve_cut.py plan edit/transcripts/video.json edit/video.edl.json

# render the cut + captions
$PY scripts/ve_render.py video.mp4 edit/video.edl.json edit/video.edited.mp4 \
    --transcript edit/transcripts/video.json

# find Shorts
$PY scripts/ve_shorts.py find edit/transcripts/video.json edit/video.shorts.json

# build post pack + HTML review page
$PY scripts/ve_postpack.py edit/transcripts/video.json edit/video.shorts.json edit/video.postpack.json
$PY scripts/ve_publish.py edit/review.html --main edit/video.edited.mp4 \
    --postpack edit/video.postpack.json --title "My Video"
```

## 4. Autopilot (set and forget)

```bash
# process everything in a folder now
.venv/bin/python scripts/autopilot.py once ~/inbox

# or keep watching for new files
.venv/bin/python scripts/autopilot.py watch ~/inbox
```

Edited videos + Shorts + review pages land in `~/inbox/edited/`.

## 5. Make it yours

Edit `taste.yaml`. Change captions, cut aggressiveness, color, B-roll sources, Shorts settings.
Re-run — your next edit matches. That's the whole customization surface.

## Troubleshooting

- **"could not transcribe"** — add `ELEVENLABS_API_KEY` to `.env` (for local files), or pass a
  URL that has captions, or supply your own transcript with `--from-transcript`.
- **"ffmpeg failed"** — install ffmpeg (`brew install ffmpeg` / `apt install ffmpeg`).
- **Captions clip on portrait video** — they auto-fit; if you set a huge `captions.size`, lower it.
- **No Shorts found** — your video may be short, or hooks are weak. Lower `shorts.min_hook_score`.
