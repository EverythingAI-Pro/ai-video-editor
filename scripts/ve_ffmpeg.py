#!/usr/bin/env python3
"""
ve_ffmpeg.py — the ffmpeg engine, with the production-correctness rules baked in.

Reused/adapted from the proven `video-use` pipeline. The hard rules that live here
(so a founder never has to know them):
  - Per-segment extract -> lossless `-c copy` concat (never single-pass).        [no double-encode]
  - 30ms audio fades at every segment boundary.                                  [no clicks/pops]
  - Color grade applied DURING extraction (so it's baked once, not re-encoded).
  - Two-pass loudnorm to -14 LUFS at the end (social standard).
  - Portrait/landscape aware scaling; HDR->SDR tonemap when needed.

These functions are imported by ve_render.py / ve_cut.py; the file is also runnable
for spot operations.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

# Named grades (from video-use). Creative looks are opt-in; default path is auto/subtle.
GRADE_PRESETS = {
    "none": "",
    "subtle": "eq=contrast=1.03:saturation=0.98",
    "neutral_punch": "eq=contrast=1.06,curves=master='0/0 0.25/0.23 0.75/0.77 1/1'",
    "warm_cinematic": (
        "eq=contrast=1.12:brightness=-0.02:saturation=0.88,"
        "colorbalance=rs=0.02:bs=-0.03:rm=0.04:bm=-0.02:rh=0.08:bh=-0.05,"
        "curves=master='0/0 0.25/0.22 0.75/0.78 1/1'"
    ),
}

FADE = 0.03  # 30ms audio fade at boundaries


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd[:6])} …\n{r.stderr[-1500:]}")
    return r


def probe(path: str) -> dict:
    r = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    info = json.loads(r.stdout or "{}")
    vstream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    w = int(vstream.get("width", 0) or 0)
    h = int(vstream.get("height", 0) or 0)
    dur = float(info.get("format", {}).get("duration", 0) or 0)
    # frame rate
    fr = vstream.get("avg_frame_rate", "0/1")
    try:
        num, den = fr.split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except Exception:
        fps = 0.0
    ct = (vstream.get("color_transfer") or "").lower()
    is_hdr = ct in ("smpte2084", "arib-std-b67")
    return {"width": w, "height": h, "duration": dur, "fps": round(fps, 3),
            "portrait": h > w, "hdr": is_hdr}


TONEMAP = "zscale=t=linear:npl=100,tonemap=hable,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"


def scale_filter(meta: dict, target_h: int = 1080) -> str:
    """Scale to target height, preserving aspect (width stays even); tonemap HDR first.

    Height-driven works for both orientations: a landscape clip becomes target_h tall
    (e.g. 1920x1080), a portrait clip becomes target_h tall (e.g. 608x1080). The render
    layer crops/letterboxes to the final canvas as needed.
    """
    base = TONEMAP + "," if meta.get("hdr") else ""
    return base + f"scale=-2:{target_h}"


def extract_segment(src: str, start: float, end: float, out: str,
                    grade: str = "", target_h: int = 1080, crf: int = 20,
                    preset: str = "fast", fps: int = 30) -> None:
    """Extract one cut with grade + 30ms audio fades baked in. Re-encoded once, here."""
    meta = probe(src)
    dur = max(round(end - start, 3), 0.04)
    vf_parts = [scale_filter(meta, target_h)]
    if grade:
        vf_parts.append(grade)
    vf_parts.append("setsar=1")
    vf = ",".join(p for p in vf_parts if p)
    af = f"afade=t=in:st=0:d={FADE},afade=t=out:st={max(dur - FADE, 0):.3f}:d={FADE}"
    run([
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
        "-vf", vf, "-af", af,
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", out,
    ])


def concat_lossless(segments: list[str], out: str) -> None:
    """Stitch already-encoded segments with zero re-encode (concat demuxer + -c copy)."""
    listfile = Path(out).with_suffix(".concat.txt")
    listfile.write_text("".join(f"file '{Path(s).resolve()}'\n" for s in segments), encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(listfile), "-c", "copy", "-movflags", "+faststart", out])
    listfile.unlink(missing_ok=True)


def loudnorm(src: str, out: str, target_lufs: float = -14.0) -> None:
    """Two-pass EBU R128 loudness normalization to social standard."""
    measure = run([
        "ffmpeg", "-hide_banner", "-i", src,
        "-af", f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11:print_format=json",
        "-f", "null", "-",
    ], check=False)
    stats = {}
    txt = measure.stderr
    if "{" in txt:
        try:
            stats = json.loads(txt[txt.rindex("{"): txt.rindex("}") + 1])
        except Exception:
            stats = {}
    af = f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11"
    if stats:
        af += (f":measured_I={stats.get('input_i')}:measured_TP={stats.get('input_tp')}"
               f":measured_LRA={stats.get('input_lra')}:measured_thresh={stats.get('input_thresh')}"
               f":offset={stats.get('target_offset')}:linear=true")
    run(["ffmpeg", "-y", "-v", "error", "-i", src, "-af", af,
         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out])


def resolve_grade(taste: dict, src: str) -> str:
    """Turn taste.color into an ffmpeg filter string. 'auto' analyzes the clip."""
    color = taste.get("color", {})
    if color.get("raw_filter"):
        return color["raw_filter"]
    g = color.get("grade", "auto")
    if g in GRADE_PRESETS:
        return GRADE_PRESETS[g]
    if g == "auto":
        return auto_grade(src)
    return ""


def auto_grade(src: str) -> str:
    """Bounded, skin-safe auto correction from sampled frame stats (video-use approach)."""
    meta = probe(src)
    dur = meta.get("duration", 0) or 1.0
    # sample brightness/saturation across the clip
    r = run([
        "ffmpeg", "-hide_banner", "-i", src, "-vf",
        "select='not(mod(n,30))',signalstats,metadata=print",
        "-frames:v", "10", "-f", "null", "-",
    ], check=False)
    ys, sats = [], []
    for line in r.stderr.splitlines():
        if "YAVG" in line:
            try:
                ys.append(float(line.split("YAVG=")[1].split()[0]) / 255.0)
            except Exception:
                pass
        if "SATAVG" in line:
            try:
                sats.append(float(line.split("SATAVG=")[1].split()[0]) / 255.0)
            except Exception:
                pass
    if not ys:
        return GRADE_PRESETS["subtle"]
    y = sum(ys) / len(ys)
    sat = (sum(sats) / len(sats)) if sats else 0.2
    contrast = 1.03
    gamma = 1.0
    if y < 0.42:
        gamma = min(1.10, 1.0 + (0.48 - y) * 0.5)
    elif y > 0.60:
        gamma = max(0.94, 1.0 - (y - 0.48) * 0.4)
    saturation = 0.98
    if sat < 0.18:
        saturation = 1.04
    contrast = max(0.94, min(1.08, contrast))
    return f"eq=contrast={contrast:.3f}:gamma={gamma:.3f}:saturation={saturation:.3f}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "probe":
        print(json.dumps(probe(sys.argv[2]), indent=2))
    elif len(sys.argv) >= 2 and sys.argv[1] == "autograde":
        print(auto_grade(sys.argv[2]))
    else:
        print("usage: ve_ffmpeg.py probe <video> | autograde <video>")
