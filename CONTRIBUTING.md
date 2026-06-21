# Contributing

Thanks for helping make the AI Video Editor better. It's intentionally simple: small Python
scripts that shell out to `ffmpeg`. No framework, no build step.

## Setup

```bash
bash install.sh
```

## Ground rules

- **Each script does one thing** and is runnable on its own. Keep them readable — a founder
  learning to code should be able to follow them.
- **`ffmpeg` is the engine.** Don't add heavy dependencies; the Python env is just Pillow, numpy,
  and requests on purpose.
- **Respect the hard rules** in `references/` (captions last, per-segment extract → concat, place
  B-roll on the word, etc.). They prevent silent quality failures.
- **Never commit `.venv/`, `.env`, or `edit/` output.** They're gitignored.
- **Prove it.** If you change the pipeline, render a real sample video and confirm the output
  before opening a PR. Don't claim a fix works without showing the output.

## Good first contributions

- More caption fonts / styles in `taste.yaml`.
- Niche hashtag presets in `ve_postpack.py`.
- A new B-roll route or a better hook pattern in `ve_shorts.py`.
- Native Windows setup in `setup.sh` / `install.sh`.
- A platform provider in `ve_post.py`.

## PRs

Fork, branch, prove it on a real video, open a PR describing what you changed and showing the
output. Keep it focused — one improvement per PR.
