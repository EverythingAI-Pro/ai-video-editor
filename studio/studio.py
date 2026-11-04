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
import random
import re
import shutil
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
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm",
             ".3gp", ".wmv", ".flv", ".m2ts", ".mpeg", ".mpg"}
STABLE_SECONDS = 10          # mtime must not change for this long
MIN_DURATION = 2.0           # anything shorter is rejected
SHORT_MAX = 15.0             # clips at or under this length are "shorts" (mixable)
MIN_SCENE = 2.0
MAX_SCENES = 10
CLIP_TARGET = 4.0            # seconds per selected scene
MASTER = (1920, 1080)
VERTICAL = (1080, 1920)
MUSIC_DIR = Path(os.environ.get("STUDIO_MUSIC", HERE / "assets" / "music"))
CALENDAR = Path(os.environ.get("STUDIO_CALENDAR", HERE / "content_calendar.json"))
CAL_STATE = HERE / ".calendar_state.json"


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
    if duration < 8:  # short clip: keep it as one scene, never chop it up
        cuts = [0.0, duration]
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
        "You are the content strategist of CyrusMiner, a Dubai mining infrastructure "
        "company. Write for short-form social video that gets saved and shared. Rules: "
        "plain confident English, short sentences. Educate, never sell. No calls to "
        "buy, invest or contact. No em dashes. No AI words like delve, unleash, "
        "elevate, game-changer, revolutionize. Never invent numbers or claims. Use "
        "ONLY these approved facts when mentioning the company: "
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
    plan = json.loads(m.group(0), strict=False) if m else {}
    plan.setdefault("title", "CyrusMiner Facility Update")
    plan.setdefault("hook", "Inside our mining facility")
    plan.setdefault("cta", "CyrusMiner. Dubai since 2016.")
    while len(plan.get("scenes", [])) < n_clips:
        plan.setdefault("scenes", []).append("CyrusMiner operations")
    return plan


# ---------------------------------------------------------------- render

def branded_frame(out: Path, size: tuple[int, int]) -> Path:
    """Logo-only branded card. No text on video, ever."""
    from PIL import Image
    w, h = size
    img = Image.new("RGB", (w, h), BLACK)
    if LOGO.exists():
        logo = Image.open(LOGO).convert("RGBA").resize((int(h * 0.34), int(h * 0.34)))
        img.paste(logo, ((w - logo.width) // 2, (h - logo.height) // 2), logo)
    img.save(out)
    return out


def pick_music() -> Path | None:
    tracks = sorted(MUSIC_DIR.glob("*.mp3")) if MUSIC_DIR.exists() else []
    return random.choice(tracks) if tracks else None


def has_audio(path: Path) -> bool:
    r = sh(["ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)])
    return bool(r.stdout.strip())


def esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def render(job: Path, src: Path, clips, plan: dict, vertical: bool, music: Path | None) -> Path:
    """Logo-only branded video. Output orientation = source orientation.
    Full frame fit on blurred bg (no zoom, nothing cut). Max quality encode."""
    W, H = VERTICAL if vertical else MASTER
    tag = "vertical" if vertical else "master"
    out = job / f"{tag}.mp4"
    n = len(clips)
    body_len = sum(c[1] - c[0] for c in clips)
    total = 2.2 + body_len + 2.6
    voice = has_audio(src)
    filters = []
    for i in range(n):
        filters.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=42[bg{i}];"
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1[fg{i}];"
            f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,fps=30,format=yuv420p,setsar=1[s{i}]")
    filters.append("".join(f"[s{i}]" for i in range(n)) +
                   f"concat=n={n}:v=1:a=0[body]")
    # logo-only open/close cards
    open_png = branded_frame(job / f"open_{tag}.png", (W, H))
    close_png = branded_frame(job / f"close_{tag}.png", (W, H))
    filters.append(f"[{n}:v]scale={W}:{H},setsar=1,fps=30,format=yuv420p[op]")
    filters.append(f"[{n + 1}:v]scale={W}:{H},setsar=1,fps=30,format=yuv420p[cl]")
    filters.append("[op][body][cl]concat=n=3:v=1:a=0[full]")
    # watermark (bottom-right), then fades. No text anywhere.
    if LOGO.exists():
        filters.append(f"movie={LOGO}[logo];[logo]scale={int(W * 0.07)}:-1[lg]")
        filters.append(f"[full][lg]overlay=W-w-{int(W * 0.03)}:H-h-{int(H * 0.03)}"
                       f",fade=t=in:st=0:d=0.6,fade=t=out:st={total - 0.8:.2f}:d=0.8[vout]")
    else:
        filters.append(f"[full]fade=t=in:st=0:d=0.6,fade=t=out:st={total - 0.8:.2f}:d=0.8[vout]")
    # inputs: clips, open, close, [music], [silence, silence if voice]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for (s, e, _m) in clips:
        cmd += ["-ss", str(s), "-t", str(e - s), "-i", str(src)]
    cmd += ["-loop", "1", "-t", "2.2", "-i", str(open_png),
            "-loop", "1", "-t", "2.6", "-i", str(close_png)]
    mi = n + 2
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    if voice:
        cmd += ["-f", "lavfi", "-t", "2.2", "-i", "anullsrc=r=48000:cl=stereo",
                "-f", "lavfi", "-t", "2.6", "-i", "anullsrc=r=48000:cl=stereo"]
        for i in range(n):
            filters.append(f"[{i}:a]aresample=48000[a{i}]")
        filters.append("".join(f"[a{i}]" for i in range(n)) +
                       f"concat=n={n}:v=0:a=1[abody]")
        if music:
            filters.append(f"[{mi}:a]atrim=0:{total:.2f},aresample=48000[mus]")
            filters.append("[abody]asplit[vd][vs];"
                           "[mus][vd]sidechaincompress=threshold=0.03:ratio=12:attack=20:release=600[musd];"
                           "[vs][musd]amix=inputs=2:duration=first[aoutbody]")
        else:
            filters.append("[abody]anull[aoutbody]")
        sa, sb = mi + 1, mi + 2
        filters.append(f"[{sa}:a]aresample=48000[sa];[{sb}:a]aresample=48000[sb];"
                       f"[sa][aoutbody][sb]concat=n=3:v=0:a=1[aout]")
    elif music:
        filters.append(f"[{mi}:a]atrim=0:{total:.2f},volume=1.0,aresample=48000[musv]")
        cmd += ["-f", "lavfi", "-t", "2.2", "-i", "anullsrc=r=48000:cl=stereo",
                "-f", "lavfi", "-t", "2.6", "-i", "anullsrc=r=48000:cl=stereo"]
        sa, sb = mi + 1, mi + 2
        filters.append(f"[{sa}:a]aresample=48000[sa];[{sb}:a]aresample=48000[sb];"
                       f"[sa][musv][sb]concat=n=3:v=0:a=1[aout]")
    else:
        cmd += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
        filters.append(f"[{mi}:a]anullsrc=r=48000:cl=stereo,atrim=0:{total:.2f}[aout]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            "-t", f"{total:.2f}", str(out)]
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
    for name in ("master.mp4", "thumbnail.jpg"):
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
            media = ([uploads["master.mp4"]] if "master.mp4" in uploads
                     else [uploads["thumbnail.jpg"]] if "thumbnail.jpg" in uploads else [])
        payload = {"type": "draft", "shortLink": False, "date": date, "tags": [],
                   "posts": [{"integration": {"id": have[plat]},
                              "value": [{"content": text, "image": media}],
                              "settings": settings}]}
        r = postiz("/api/public/v1/posts", POSTIZ_KEY, payload=payload)
        made.append(f"{plat}:{'ok' if r.ok else r.status_code} {'' if r.ok else r.text[:250]}")
    return ", ".join(made) or "no matching integrations"


# ---------------------------------------------------------------- content calendar

def next_topic() -> str:
    """Next unused topic from the 30-day viral calendar (rotates)."""
    try:
        topics = json.loads(CALENDAR.read_text())
    except Exception:
        return ""
    if not topics:
        return ""
    try:
        state = json.loads(CAL_STATE.read_text())
    except Exception:
        state = {"next": 0}
    idx = int(state.get("next", 0)) % len(topics)
    t = topics[idx]
    state["next"] = idx + 1
    CAL_STATE.write_text(json.dumps(state))
    return (f"TOPIC: {t.get('topic', '')}\n"
            f"KEY FACT TO SHARE: {t.get('fact', '')}\n"
            f"ANGLE: {t.get('angle', '')}")


# ---------------------------------------------------------------- pipeline
def stable(path: Path, seconds: int = STABLE_SECONDS) -> bool:
    try:
        s1 = (path.stat().st_size, path.stat().st_mtime)
        time.sleep(seconds)
        s2 = (path.stat().st_size, path.stat().st_mtime)
        return s1 == s2
    except OSError:
        return False


def run_job(stem: str, video: Path, brief: str, platforms: list[str]) -> bool:
    job = READY / stem / "studio"
    job.mkdir(parents=True, exist_ok=True)
    done = job / "studio.done"
    if done.exists():
        return False
    log(f"job {stem}: started")
    try:
        duration = ffprobe_duration(video)
        if duration < MIN_DURATION:
            raise RuntimeError(f"video shorter than {MIN_DURATION:.0f} seconds")
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
        w, h = probe_size(video)
        vertical = h > w
        master = render(job, video, clips, plan, vertical, pick_music())
        vert = None  # single output, matched to source orientation
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


def process(video: Path) -> bool:
    stem = video.stem
    marker = RAW / f"{stem}.studio"
    brief_file = RAW / f"{stem}.brief.txt"
    plat_file = RAW / f"{stem}.platforms.txt"
    brief = brief_file.read_text(encoding="utf-8", errors="replace").strip() if brief_file.exists() else ""
    if not brief:
        brief = next_topic()
    platforms = [p for p in plat_file.read_text().split(",")] if plat_file.exists() else []
    try:
        ok = run_job(stem, video, brief, platforms)
        if ok:
            archive = ROOT / "05_ARCHIVE"
            archive.mkdir(exist_ok=True)
            target = archive / video.name
            i = 1
            while target.exists():
                target = archive / f"{video.stem}-{i}{video.suffix}"
                i += 1
            shutil.move(str(video), target)
            for suffix in (".brief.txt", ".platforms.txt"):
                (RAW / f"{stem}{suffix}").unlink(missing_ok=True)
        return ok
    finally:
        marker.unlink(missing_ok=True)


def process_mix(marker: Path) -> bool:
    """Merge all short clips (2-15s) currently in RAW into one branded video."""
    vids: list[Path] = []
    for f in sorted(RAW.iterdir()):
        if not (f.is_file() and f.suffix.lower() in VIDEO_EXT and not f.name.startswith(".")):
            continue
        if not (RAW / f"{f.stem}.mix").exists():
            continue  # only clips the user marked for mixing
        if (READY / f.stem / "studio" / "studio.done").exists():
            continue  # already produced individually
        d = ffprobe_duration(f)
        if MIN_DURATION <= d <= SHORT_MAX:
            vids.append(f)
    vids = [v for v in vids if stable(v)]
    if len(vids) < 2:
        log("mix: need at least 2 short clips (2-15s); marker kept for later")
        return False
    stem = "mix-" + time.strftime("%Y%m%d-%H%M%S")
    job = READY / stem / "studio"
    job.mkdir(parents=True, exist_ok=True)
    mixed = job / "mixed_source.mp4"
    lst = job / "concat.txt"
    lst.write_text("".join(f"file '{v.absolute()}'\n" for v in vids), encoding="utf-8")
    r = sh(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
            "-c:v", "libx264", "-preset", "fast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(mixed)])
    if r.returncode != 0 or not mixed.exists():
        log(f"mix: concat failed: {r.stderr[-500:]}")
        return False
    brief = ""
    for v in vids:
        bf = RAW / f"{v.stem}.brief.txt"
        if bf.exists() and bf.read_text(encoding="utf-8", errors="replace").strip():
            brief = bf.read_text(encoding="utf-8", errors="replace").strip()
            break
    ok = run_job(stem, mixed, brief, [])
    if ok:
        archive = ROOT / "05_ARCHIVE"
        archive.mkdir(exist_ok=True)
        for v in vids:
            for suffix in (".studio", ".mix", ".brief.txt", ".platforms.txt"):
                (RAW / f"{v.stem}{suffix}").unlink(missing_ok=True)
            shutil.move(str(v), archive / v.name)
        marker.unlink(missing_ok=True)
        log(f"mix: merged {len(vids)} clips -> {job}")
    return ok


def find_video(stem: str) -> Path | None:
    for f in RAW.iterdir():
        if f.is_file() and f.stem == stem and f.suffix.lower() in VIDEO_EXT:
            return f
    return None


def scan_once() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    n = 0
    for marker in sorted(RAW.glob("*.mix")):
        try:
            process_mix(marker)
        except Exception as exc:
            log(f"mix error: {exc}")
    for marker in sorted(RAW.glob("*.studio")):
        video = find_video(marker.stem)
        if video is not None and stable(video):
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
    log(f"watching {RAW} for *.studio / *.mix markers every {args.interval}s")
    while True:
        try:
            scan_once()
        except Exception as exc:
            log(f"scan error: {exc}")
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
