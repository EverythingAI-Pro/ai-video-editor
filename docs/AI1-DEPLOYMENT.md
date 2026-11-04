# AI1 Production Deployment — CyrusMiner Social AI Agent

The AI Video Editor runs in production on **AI1** (GPU box) as part of the social AI
agent stack. Raw video goes in one end; edited, captioned Shorts and scheduled
multi-platform posts come out the other.

## Architecture

```
You (web) ──drop raw video──▶ AI1: 01_RAW ─▶ content-editor ─▶ 02_READY ─▶ 03_POSTIZ_UPLOAD
                                   (Whisper CUDA + ai-video-editor: cut, captions, shorts, postpack)
https://video.nimaataie.com (web dashboard, port 4010)        │
                                                               ▼ Postiz API
                                        Postiz (port 4007) — https://postiz.nimaataie.com
                                        X · YouTube · Telegram · Nostr · Hashnode · Dribbble · Mastodon
```

Both public URLs reach AI1 through a single **Cloudflare Tunnel** (`ai1`),
running as `cloudflared-postiz.service`. DNS + TLS are managed by Cloudflare.

## Components

| Piece | Path on AI1 | Service | Port | Public URL |
|---|---|---|---|---|
| Postiz (scheduler/publisher) | `~/postiz` | docker compose | 4007 | https://postiz.nimaataie.com |
| AI Video Editor (engine, this repo) | `~/content-engine/ai-video-editor` | — (invoked by pipeline) | — | — |
| Content pipeline (autopilot) | `~/content-engine/content_pipeline_watch.py` | `content-editor.service` (user) | — | — |
| Web dashboard | `~/content-engine/video_web.py` | `video-web.service` (user) | 4010 | https://video.nimaataie.com |
| Content folders | `~/content-system/{01_RAW,02_READY,03_POSTIZ_UPLOAD,04_PUBLISHED}` | — | — | — |
| Cloudflare Tunnel | token in unit | `cloudflared-postiz.service` (system) | — | — |

AI1 is reachable over Tailscale at `100.123.141.71` (user `cyrusminer`).

## Files in this repo

| Path | What it is |
|---|---|
| `web/app.py` | Web dashboard: upload, live status, players, review page, post copy, retry |
| `web/video-web.service` | systemd user unit for the dashboard |
| `pipeline/content_pipeline_watch.py` | Autopilot: watches 01_RAW, Whisper-CUDA transcribe, runs `scripts/edit.py`, stages outputs |
| `pipeline/content-editor.service` | systemd user unit for the autopilot |
| `deploy/sync-content-ai1.sh` | Mac-side helper: push/pull videos, check status |

## Daily use

1. Open **https://video.nimaataie.com** → sign in → drop a raw talking-head video.
2. ~30 s later the autopilot picks it up: transcribe → cut filler → captions →
   color → Shorts → post pack → review page.
3. Open the job → **Review page** tab → watch, tweak `taste.yaml` if needed.
4. Finished clips land in `03_POSTIZ_UPLOAD` → schedule in **Postiz**.

## Operations

```bash
systemctl --user status content-editor.service video-web.service
journalctl --user -u content-editor.service -f
systemctl --user restart video-web.service content-editor.service
sudo systemctl status cloudflared-postiz.service
```

Adding another public hostname: Cloudflare Zero Trust → Networks → Tunnels →
`ai1` → Public Hostname → Add (service URL = AI1 Tailscale IP + port).

## Secrets (never committed)

- Dashboard password — env `VIDEO_WEB_PASSWORD` in `video-web.service` on AI1
- Tunnel token — `cloudflared-postiz.service` unit on AI1
- Postiz env / social API keys — `~/postiz/.env` on AI1
- SSH deploy key — ops workspace only
