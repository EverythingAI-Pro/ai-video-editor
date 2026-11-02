#!/usr/bin/env python3
"""CyrusMiner Content Studio — silent-footage to branded marketing video.

MVP orchestrator. Uses ONLY existing components:
  ffmpeg (scene detect, cut, grade, drawtext, watermark) + Pillow (branded frames,
  thumbnail) + existing local Qwen (creative plan, captions, post copy) + Postiz API.

Job protocol (single official upload = video dashboard):
  01_RAW/<name>.mp4          raw video (never modified)
  01_RAW/<name>.studio       marker: route to studio (absent -> talking-head pipeline)
  01_RAW/<name>.brief.txt    optional creative direction from the user/Kimi
  01_RAW/<name>.platforms.txt optional comma list, e.g. youtube,x,telegram

Outputs (job dir 02_READY/<name>/studio/):
  master.mp4 16:9 branded video, vertical.mp4 9:16, thumbnail.jpg,
  postcopy.json, plan.json, studio.log
Then stages master/vertical/thumbnail into 03_POSTIZ_UPLOAD/<name>/
and creates unpublished Postiz drafts (+1 day) if POSTIZ_API_KEY is set.

Reliability rules: one video = one job; size-stable detection; originals never
touched; resume skips finished steps; never publishes (drafts only).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("CONTENT_ROOT", Path.home() / "content-system")).resolve()
RAW = ROOT / "01_RAW"
READY = ROOT / "02_READY"
UPLOAD = ROOT / "03_POSTIZ_UPLOAD"

QWEN = os.environ.get("STUDIO_QWEN", "huihui_ai/Qwen3.8-abliterated:latest")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
POSTIZ = os.environ.get("POSTIZ_URL", "http://100.123.141.71:4007")
POSTIZ_KEY = os.environ.get("POSTIZ_API_KEY", "")
LOGO = Path(os.environ.get("STUDIO_LOGO", HERE / "assets" / "cyrus-icon-1024.png"))
FACTS = Path(os.environ.get("STUDIO_FACTS", HERE / "facts.yaml"))

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
GOLD = "#D4AF37"
BLACK = "#0A0A0A"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
STABLE_SECONDS = 10          # mtime must not change for this long
MIN_SCENE = 2.0
MAX_SCENES = 10
CLIP_TARGET = 4.0            # seconds per selected scene
MASTER = (1920, 1080)
VERTICAL = (1080, 1920)


def log(msg: str) -> None:
    print(f"[studio] {msg}", flush=True)


def sh(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, **kw)


def ffprobe_duration(path: Path) -> float:
    r = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def probe_size(path: Path) -> tuple[int, int]:
    r = sh(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=width,height", "-of", "csv=p=0", str(path)])
    try:
        w, h = r.stdout.strip().splitlines()[0].split(",")[:2]
        return int(w), int(h)
    except Exception:
        return 1920, 1080


def scene_cuts(path: Path, duration: float) -> list[float]:
    """Scene-change timestamps via ffprobe scene score; fallback to even splits."""
    r = sh(["ffprobe", "-v", "error", "-f", "lavfi",
            "-i", f"movie={path},select='gt(scene,0.4)'",
            "-show_entries", "frame=pkt_pts_time", "-of", "csv=p=0"])
    cuts = [0.0]
    for line in r.stdout.splitlines():
        try:
            t = float(line.strip())
        except ValueError:
            continue
        if MIN_SCENE <= t <= duration - MIN_SCENE and t - cuts[-1] >= MIN_SCENE:
            cuts.append(t)
    if len(cuts) < 3:  # static footage: even segments
        n = max(3, min(MAX_SCENES, int(duration // CLIP_TARGET)))
        cuts = [duration * i / n for i in range(n)] + [duration]
    return cuts


def pick_clips(cuts: list[float]) -> list[tuple[float, float, float]]:
    """(start, end, midpoint) per scene, trimmed to CLIP_TARGET around midpoint."""
    clips = []
    for i in range(len(cuts) - 1):
        s, e = cuts[i], cuts[i + 1]
        mid = (s + e) / 2
        cs, ce = max(s, mid - CLIP_TARGET / 2), min(e, mid + CLIP_TARGET / 2)
        if ce - cs >= 1.0:
            clips.append((round(cs, 2), round(ce, 2), round((cs + ce) / 2, 2)))
    return clips[:MAX_SCENES]


# ---------------------------------------------------------------- Qwen

def qwen_chat(system: str, user: str, temperature: float = 0.6) -> str:
    payload = {"model": QWEN, "think": False, "stream": False,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "options": {"temperature": temperature, "num_ctx": 8192}}
    r = subprocess.run(["curl", "-s", "-m", "180", f"{OLLAMA}/api/chat",
                        "-d", json.dumps(payload)], capture_output=True, text=True)
    data = json.loads(r.stdout)
    return (data.get("message") or {}).get("content", "").strip()


def load_facts() -> str:
    if not FACTS.exists():
        return "company: CyrusMiner, www.cyrusminer.com (no other claims approved)"
    return FACTS.read_text(encoding="utf-8")


def build_plan(duration: float, n_clips: int, brief: str, facts: str) -> dict:
    system = (
        "You are the creative director of CyrusMiner, a Dubai mining company. "
        "Write for short-form corporate video. Rules: plain confident English, short "
        "sentences. Never use em dashes. Never use AI words like delve, unleash, "
        "elevate, game-changer, revolutionize. Never invent numbers, capacity, specs "
        "or claims. Use ONLY these approved facts when mentioning the company: "
        + facts.replace("\n", " ")
    )
    user = (
        f"Raw footage: {duration:.0f}s of mining-facility video, no speech. "
        f"I will cut it into {n_clips} scenes of about {CLIP_TARGET:.0f}s each.\n"
        + (f"CREATIVE DIRECTION from client: {brief}\nFollow it for style, mood and emphasis.\n"
           if brief else "No direction given: choose a professional cinematic corporate angle.\n")
        + 'Reply with ONLY valid JSON (no markdown fences): '
          '{"title": "short video title", "hook": "one line opening caption (max 8 words)", '
          '"cta": "one line closing caption (max 8 words)", '
          '"scenes": ["caption for scene 1", ...], '
          '"youtube": {"title": "...", "description": "2-3 plain sentences plus hashtags"}, '
          '"x": "post text <= 250 chars", '
          '"telegram": "caption text", '
          '"mastodon": "caption text"}\n'
          f'"scenes" must have exactly {n_clips} entries, each max 8 words, '
          "concrete, about what is visible (machines, cooling, technicians, facility). "
          "hashtags: 4-6 like #BitcoinMining #CyrusMiner #Dubai #CryptoMining"
    )
    raw = qwen_chat(system, user)
    m = re.search(r"\{.*\}", raw, re.S)
    plan = json.loads(m.group(0)) if m else {}
    plan.setdefault("title", "CyrusMiner Facility Update")
    plan.setdefault("hook", "Inside our mining facility")
    plan.setdefault("cta", "CyrusMiner. Dubai since 2016.")
    while len(plan.get("scenes", [])) < n_clips:
        plan.setdefault("scenes", []).append("CyrusMiner operations")
    return plan


# ---------------------------------------------------------------- render

def branded_frame(out: Path, size: tuple[int, int], line1: str, line2: str = "") -> Path:
    from PIL import Image, ImageDraw, ImageFont
    w, h = size
    img = Image.new("RGB", (w, h), BLACK)
    dr = ImageDraw.Draw(img)
    f_big = ImageFont.truetype(FONT, int(h * 0.075))
    f_sm = ImageFont.truetype(FONT, int(h * 0.032))
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA").resize((int(h * 0.22), int(h * 0.22)))
        img.paste(logo, ((w - logo.width) // 2, int(h * 0.24)), logo)
    y = h * 0.56
    for txt, fnt in ((line1, f_big), (line2, f_sm)):
        if not txt:
            continue
        bb = dr.textbbox((0, 0), txt, font=fnt)
        dr.text(((w - (bb[2] - bb[0])) / 2, y), txt, font=fnt, fill=GOLD)
        y += (bb[3] - bb[1]) * 1.9
    img.save(out)
    return out


def esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render(job: Path, src: Path, clips, plan: dict, vertical: bool) -> Path:
    """Assemble branded video. Returns output path."""
    W, H = VERTICAL if vertical else MASTER
    tag = "vertical" if vertical else "master"
    out = job / f"{tag}.mp4"
    n = len(clips)
    body_len = sum(c[1] - c[0] for c in clips)
    total = 2.2 + body_len + 2.6
    # scene normalization (cover crop) + concat body
    filters = []
    for i in range(n):
        filters.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1,fps=30,format=yuv420p[s{i}]")
    filters.append("".join(f"[s{i}]" for i in range(n)) +
                   f"concat=n={n}:v=1:a=0[body]")
    # branded open/close frames
    open_png = branded_frame(job / f"open_{tag}.png", (W, H), "CYRUSMINER", "www.cyrusminer.com")
    close_png = branded_frame(job / f"close_{tag}.png", (W, H), plan["cta"].upper(), "CYRUSMINER · DUBAI · UAE")
    filters.append(f"[{n}:v]scale={W}:{H},setsar=1,fps=30,format=yuv420p[op]")
    filters.append(f"[{n + 1}:v]scale={W}:{H},setsar=1,fps=30,format=yuv420p[cl]")
    filters.append("[op][body][cl]concat=n=3:v=1:a=0[full]")
    # captions: hook on first scene, one caption per scene
    fsize = int(H * (0.038 if vertical else 0.048))
    y_main = int(H * (0.80 if vertical else 0.86))
    hook_y = int(H * 0.12)
    chain = "[full]"
    texts = [(plan["hook"], hook_y, 2.2, clips[0][1] - clips[0][0])]
    t_cursor = 2.2
    for i, (s, e, _m) in enumerate(clips):
        texts.append((plan["scenes"][i], y_main, t_cursor, e - s))
        t_cursor += e - s
    for i, (txt, y, start, dur) in enumerate(texts):
        filters.append(
            f"{chain}drawtext=fontfile={FONT}:text='{esc(txt)}':fontsize={fsize}:"
            f"fontcolor={GOLD}:x=(w-text_w)/2:y={y}:box=1:boxcolor=black@0.45:"
            f"boxborderw={int(fsize * 0.5)}:enable='between(t,{start:.2f},{start + dur:.2f})'[c{i}]")
        chain = f"[c{i}]"
    # watermark (bottom-right), then fades
    if LOGO.exists():
        filters.append(f"movie={LOGO}[logo];[logo]scale={int(W * 0.06)}:-1[lg]")
        filters.append(f"{chain}[lg]overlay=W-w-{int(W * 0.03)}:H-h-{int(H * 0.03)}"
                       f",fade=t=in:st=0:d=0.6,fade=t=out:st={total - 0.8:.2f}:d=0.8[vout]")
    else:
        filters.append(f"{chain}fade=t=in:st=0:d=0.6,fade=t=out:st={total - 0.8:.2f}:d=0.8[vout]")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for (s, e, _m) in clips:
        cmd += ["-ss", str(s), "-t", str(e - s), "-i", str(src)]
    cmd += ["-loop", "1", "-t", "2.2", "-i", str(open_png),
            "-loop", "1", "-t", "2.6", "-i", str(close_png)]
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-t", f"{total:.2f}", str(out)]
    r = sh(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed: {r.stderr[-2000:]}")
    return out


def make_thumbnail(job: Path, src: Path, clips, plan: dict) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    best = max(clips, key=lambda c: c[1] - c[0])
    frame = job / "thumb_frame.jpg"
    sh(["ffmpeg", "-y", "-v", "error", "-ss", str(best[2]), "-i", str(src),
        "-frames:v", "1", str(frame)])
    img = Image.open(frame).convert("RGB").resize(MASTER)
    dr = ImageDraw.Draw(img, "RGBA")
    dr.rectangle([0, MASTER[1] - 300, MASTER[0], MASTER[1]], fill=(0, 0, 0, 150))
    f_t = ImageFont.truetype(FONT, 84)
    f_s = ImageFont.truetype(FONT, 40)
    dr.text((60, MASTER[1] - 250), plan["title"][:48], font=f_t, fill=GOLD)
    dr.text((60, MASTER[1] - 120), "CyrusMiner · www.cyrusminer.com", font=f_s, fill="#E8E8E8")
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA").resize((140, 140))
        img.paste(logo, (MASTER[0] - 200, 40), logo)
    out = job / "thumbnail.jpg"
    img.save(out, quality=90)
    return out


# ---------------------------------------------------------------- Postiz

def postiz(path: str, key: str, payload: dict | None = None, files: dict | None = None):
    import urllib.request
    import requests as rq
    h = {"Authorization": key}
    url = f"{POSTIZ}{path}"
    if files:
        r = rq.post(url, headers=h, files=files)
    else:
        r = rq.post(url, headers=h, json=payload or {})
    return r


def create_postiz_drafts(job: Path, plan: dict, platforms: list[str]) -> str:
    if not POSTIZ_KEY:
        return "no POSTIZ_API_KEY set; skipped"
    import requests as rq
    from datetime import datetime, timezone
    h = {"Authorization": POSTIZ_KEY}
    ints = rq.get(f"{POSTIZ}/api/public/v1/integrations", headers=h, timeout=30).json()
    have = {i["identifier"].lower(): i["id"] for i in ints}
    wanted = [p.strip().lower() for p in platforms] if platforms else ["youtube", "x", "mastodon"]
    uploads = {}
    for name in ("master.mp4", "vertical.mp4", "thumbnail.jpg"):
        p = job / name
        if p.exists():
            r = postiz("/api/public/v1/media", POSTIZ_KEY,
                       files={"file": (name, open(p, "rb"), "video/mp4" if p.suffix == ".mp4" else "image/jpeg")})
            if r.ok:
                j = r.json()
                uploads[name] = {"id": str(j.get("id") or name), "path": j["path"]}
    date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    made = []
    for plat in wanted:
        if plat not in have:
            continue
        copy = plan.get(plat) or {}
        if plat == "youtube":
            text = copy.get("description", "")
            settings = {"__type": "youtube", "title": copy.get("title", plan["title"]),
                        "type": "public", "selfDeclaredMadeForKids": "no", "tags": []}
            if "thumbnail.jpg" in uploads:
                settings["thumbnail"] = uploads["thumbnail.jpg"]
            media = [uploads["master.mp4"]] if "master.mp4" in uploads else []
        else:
            text = copy.get("text", "") if isinstance(copy, dict) else str(copy)
            settings = {"__type": plat}
            if plat == "x":
                settings["who_can_reply_post"] = "everyone"
            vid = "vertical.mp4" if plat == "x" else "master.mp4"
            media = ([uploads[vid]] if vid in uploads
                     else [uploads["thumbnail.jpg"]] if "thumbnail.jpg" in uploads else [])
        payload = {"type": "draft", "shortLink": False, "date": date, "tags": [],
                   "posts": [{"integration": {"id": have[plat]},
                              "value": [{"content": text, "image": media}],
                              "settings": settings}]}
        r = postiz("/api/public/v1/posts", POSTIZ_KEY, payload=payload)
        made.append(f"{plat}:{'ok' if r.ok else r.status_code} {'' if r.ok else r.text[:250]}")
    return ", ".join(made) or "no matching integrations"


# ---------------------------------------------------------------- pipeline

def stable(path: Path, seconds: int = STABLE_SECONDS) -> bool:
    try:
        s1 = (path.stat().st_size, path.stat().st_mtime)
        time.sleep(seconds)
        s2 = (path.stat().st_size, path.stat().st_mtime)
        return s1 == s2
    except OSError:
        return False


def process(video: Path) -> bool:
    stem = video.stem
    marker = RAW / f"{stem}.studio"
    brief_file = RAW / f"{stem}.brief.txt"
    plat_file = RAW / f"{stem}.platforms.txt"
    job = READY / stem / "studio"
    job.mkdir(parents=True, exist_ok=True)
    done = job / "studio.done"
    if done.exists():
        return False
    log(f"job {stem}: started")
    brief = brief_file.read_text(encoding="utf-8", errors="replace").strip() if brief_file.exists() else ""
    platforms = [p for p in plat_file.read_text().split(",")] if plat_file.exists() else []
    try:
        duration = ffprobe_duration(video)
        if duration < 5:
            raise RuntimeError("video shorter than 5 seconds")
        cuts = scene_cuts(video, duration)
        clips = pick_clips(cuts)
        facts = load_facts()
        plan_file = job / "plan.json"
        if plan_file.exists():
            plan = json.loads(plan_file.read_text())
        else:
            plan = build_plan(duration, len(clips), brief, facts)
            plan_file.write_text(json.dumps(plan, indent=2))
        while len(plan.get("scenes", [])) < len(clips):
            plan.setdefault("scenes", []).append("CyrusMiner operations")
        master = render(job, video, clips, plan, vertical=False)
        vert = render(job, video, clips, plan, vertical=True) if duration >= 15 else None
        thumb = make_thumbnail(job, video, clips, plan)
        (job / "postcopy.json").write_text(json.dumps(
            {k: plan[k] for k in ("title", "hook", "cta", "youtube", "x", "telegram", "mastodon")
             if k in plan}, indent=2))
        stage = UPLOAD / stem
        stage.mkdir(parents=True, exist_ok=True)
        for p in [master, vert, thumb]:
            if p:
                (stage / p.name).write_bytes(p.read_bytes())
        postiz_result = create_postiz_drafts(job, plan, platforms)
        (job / "postiz.txt").write_text(postiz_result)
        done.write_text("ok\n")
        log(f"job {stem}: done -> {job} | postiz: {postiz_result}")
        return True
    except Exception as exc:
        (job / "studio.failed").write_text(str(exc))
        log(f"job {stem}: FAILED {exc}")
        return False
    finally:
        marker.unlink(missing_ok=True)


def scan_once() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    n = 0
    for marker in sorted(RAW.glob("*.studio")):
        video = RAW / (marker.stem + ".mp4")
        if not video.exists():
            for ext in VIDEO_EXT - {".mp4"}:
                if (RAW / (marker.stem + ext)).exists():
                    video = RAW / (marker.stem + ext)
                    break
        if video.exists() and stable(video):
            if process(video):
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    args = ap.parse_args()
    if args.once:
        log(f"processed {scan_once()} job(s)")
        return 0
    log(f"watching {RAW} for *.studio markers every {args.interval}s")
    while True:
        try:
            scan_once()
        except Exception as exc:
            log(f"scan error: {exc}")
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
