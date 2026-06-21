#!/usr/bin/env python3
"""
ve_render.py — assemble the final video from the EDL.

Order is the production-correct order (from video-use's hard rules):
  1. Extract each kept range -> per-segment mp4 (grade + 30ms audio fades baked in)
  2. Lossless concat -> base.mp4   (the tightened talking head)
  3. Lay B-roll cutaways over the base during their windows (visual swap, audio untouched)
  4. Burn captions LAST (PIL overlay PNGs — this ffmpeg has no libass)
  5. Optional endcard, optional logo watermark
  6. Two-pass loudnorm to -14 LUFS

Everything is taste-driven. The function `render()` is the single entry the orchestrator calls.

CLI:
  ve_render.py <source.mp4> <edl.json> <out.mp4>
      [--transcript t.json] [--broll broll_plan.json] [--taste taste.yaml] [--no-captions]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ve_ffmpeg as ff  # noqa: E402
from ve_taste import load_taste  # noqa: E402


def _target_h(taste: dict, meta: dict) -> int:
    res = taste.get("output", {}).get("resolution", "match")
    if res == "1080p":
        return 1080
    if res == "720p":
        return 720
    return min(1080, meta["height"]) if meta["height"] else 1080


def _fps(taste: dict, meta: dict) -> int:
    f = taste.get("output", {}).get("fps", "match")
    if isinstance(f, int):
        return f
    return int(round(meta["fps"])) if meta.get("fps") else 30


def build_base(source: str, edl: dict, taste: dict, work: Path) -> tuple[str, dict]:
    """Steps 1-2: extract kept ranges with grade+fades, lossless concat."""
    meta = ff.probe(source)
    th = _target_h(taste, meta)
    fps = _fps(taste, meta)
    grade = ff.resolve_grade(taste, source)
    segdir = work / "segments"
    segdir.mkdir(parents=True, exist_ok=True)
    segs = []
    for i, rng in enumerate(edl["ranges"]):
        out = str(segdir / f"seg_{i:04d}.mp4")
        ff.extract_segment(source, rng["source_start"], rng["source_end"], out,
                           grade=grade, target_h=th, fps=fps)
        segs.append(out)
    if not segs:
        raise RuntimeError("EDL has no ranges to render")
    base = str(work / "base.mp4")
    if len(segs) == 1:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", segs[0], "-c", "copy", base], check=True)
    else:
        ff.concat_lossless(segs, base)
    return base, {"target_h": th, "fps": fps, **meta}


def overlay_broll(base: str, broll_plan: dict, taste: dict, work: Path) -> str:
    """Step 3: replace the base's VISUAL during each b-roll window; audio untouched.

    Adapted from b-roll-finder/render_cutaways.py — segment-concat the base video,
    swapping the picture for the cutaway clip during [start,end] (output timeline).
    """
    beats = broll_plan.get("beats", []) if broll_plan else []
    placed = [b for b in beats if b.get("asset") and Path(b["asset"]).is_file()]
    if not placed:
        return base

    meta = ff.probe(base)
    W, H, FPS = meta["width"], meta["height"], int(round(meta["fps"]) or 30)
    end = meta["duration"]
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{H},setsar=1,fps={FPS},format=yuv420p")
    silent = taste.get("broll", {}).get("audio", "silent") == "silent"

    fmt = work / "broll_fmt"
    fmt.mkdir(parents=True, exist_ok=True)
    placed = sorted(placed, key=lambda b: b["start"])
    inputs = ["-i", base]
    for i, b in enumerate(placed):
        dur = round(b["end"] - b["start"], 3)
        clip = str(fmt / f"b{i:03d}.mp4")
        src_in = float(b.get("src_in", 0.0))
        if b.get("kind") == "still":
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", str(dur),
                            "-i", b["asset"], "-vf", vf, "-an", "-c:v", "libx264",
                            "-crf", "18", "-preset", "fast", clip], check=True)
        else:
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(src_in), "-t", str(dur),
                            "-i", b["asset"], "-vf", vf, "-an", "-c:v", "libx264",
                            "-crf", "18", "-preset", "fast", clip], check=True)
        b["_clip"] = clip
        inputs += ["-i", clip]

    parts, fg, last, seg = [], [], 0.0, 0
    for i, b in enumerate(placed):
        s, e = b["start"], b["end"]
        if s > last + 0.01:
            fg.append(f"[0:v]trim=start={last}:end={s},setpts=PTS-STARTPTS,{vf}[g{seg}]")
            parts.append(f"[g{seg}]")
            seg += 1
        fg.append(f"[{i + 1}:v]setpts=PTS-STARTPTS[c{i}]")
        parts.append(f"[c{i}]")
        last = e
    if last < end - 0.01:
        fg.append(f"[0:v]trim=start={last}:end={end},setpts=PTS-STARTPTS,{vf}[g{seg}]")
        parts.append(f"[g{seg}]")
    fg.append("".join(parts) + f"concat=n={len(parts)}:v=1:a=0[v]")
    fg.append(f"[0:a]atrim=start=0:end={end},asetpts=PTS-STARTPTS[a]")
    out = str(work / "with_broll.mp4")
    cmd = (["ffmpeg", "-y", "-v", "error"] + inputs +
           ["-filter_complex", ";".join(fg), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"b-roll overlay failed:\n{r.stderr[-1500:]}")
    return out


def add_endcard(video: str, taste: dict, work: Path) -> str:
    ec = taste.get("brand", {}).get("endcard", {})
    if not ec.get("enabled"):
        return video
    from PIL import Image, ImageDraw  # noqa
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ve_captions import _font_path_and_index  # reuse font resolver

    meta = ff.probe(video)
    W, H, FPS = meta["width"], meta["height"], int(round(meta["fps"]) or 30)
    secs = int(ec.get("seconds", 3))
    bg = ec.get("background", "#0A0A0A")
    png = work / "endcard.png"
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    font = _font_path_and_index(taste.get("captions", {}).get("font", "HelveticaNeue-Bold"), int(H * 0.06))
    headline = ec.get("headline", "")
    if headline:
        box = d.textbbox((0, 0), headline, font=font)
        d.text(((W - (box[2] - box[0])) / 2, (H - (box[3] - box[1])) / 2), headline,
               font=font, fill=taste.get("brand", {}).get("primary_color", "#FFFFFF"))
    img.save(png)
    card = work / "endcard.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", str(secs), "-i", str(png),
                    "-f", "lavfi", "-t", str(secs), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(card)], check=True)
    # concat video + card (re-encode card was matched to video params)
    out = str(work / "with_endcard.mp4")
    norm = work / "main_norm.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video, "-vf", f"fps={FPS},format=yuv420p",
                    "-c:v", "libx264", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(norm)],
                   check=True)
    ff.concat_lossless([str(norm), str(card)], out)
    return out


def render(source: str, edl: dict, taste: dict, out: str,
           transcript: dict | None = None, broll_plan: dict | None = None,
           captions: bool = True, work_dir: str | None = None) -> str:
    work = Path(work_dir) if work_dir else Path(out).parent / "_ve_work"
    work.mkdir(parents=True, exist_ok=True)

    base, _info = build_base(source, edl, taste, work)
    video = overlay_broll(base, broll_plan or {}, taste, work)

    if captions and transcript and taste.get("captions", {}).get("enabled", True):
        from ve_captions import build_captions, burn
        lines = build_captions(transcript, edl, taste)
        capped = str(work / "captioned.mp4")
        burn(video, lines, taste, capped)
        video = capped

    video = add_endcard(video, taste, work)

    target_lufs = float(taste.get("output", {}).get("loudness_lufs", -14))
    ff.loudnorm(video, out, target_lufs=target_lufs)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("edl")
    ap.add_argument("out")
    ap.add_argument("--transcript", default=None)
    ap.add_argument("--broll", default=None)
    ap.add_argument("--taste", default=None)
    ap.add_argument("--no-captions", action="store_true")
    args = ap.parse_args()

    taste = load_taste(args.taste)
    edl = json.loads(Path(args.edl).read_text())
    transcript = json.loads(Path(args.transcript).read_text()) if args.transcript else None
    broll_plan = json.loads(Path(args.broll).read_text()) if args.broll and Path(args.broll).is_file() else None

    out = render(args.source, edl, taste, args.out, transcript=transcript,
                 broll_plan=broll_plan, captions=not args.no_captions)
    meta = ff.probe(out)
    print(f"RENDERED {out}  ({meta['width']}x{meta['height']}, {meta['duration']:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
