#!/usr/bin/env python3
"""
ve_captions.py — burn captions WITHOUT libass (this machine's ffmpeg has none).

We render each caption line as a transparent PNG with PIL, then overlay PNGs onto the
video timed to the words (ffmpeg `overlay ... enable='between(t,a,b)'`). This is the
documented, proven path from video-use ("homebrew ffmpeg has NO libass -> PIL overlay").

Taste-driven (from taste.yaml -> captions.*):
  - font (name or path), size, case (upper/sentence/lower), words_per_line, position
  - text/outline color, emotion highlight (pain=orange, money=green)
  - force_spelling: fix words ASR always mishears (RizzDial, Skool, your brand terms)

Known font quirk carried from video-use: macOS HelveticaNeue.ttc is a collection;
the BOLD face is not index 0. We probe the collection for the right face by name.

CLI:
  ve_captions.py build  <transcript.json> <edl.json> <out.srtlike.json> --taste taste.yaml
  ve_captions.py burn    <in.mp4> <captions.json> <out.mp4> --taste taste.yaml
  ve_captions.py selftest <out_dir>          # renders sample caption PNGs, no video needed
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── word classification for emotion coloring (from podcast-ad-clipper theory) ──
PAIN_WORDS = {"lost", "lose", "losing", "fail", "failed", "failing", "struggle", "struggling",
              "broken", "break", "dying", "dead", "slow", "manual", "leak", "leaking", "waste",
              "wasted", "stuck", "hard", "pain", "painful", "expensive", "miss", "missed", "drop"}
MONEY_RE = re.compile(r"(\$|%|\bmillion|\bmillions|\bscale|\bsaved|\bsave|\bdoubled|\bdouble|"
                      r"\bfree|\bgrowth|\bgrew|\brevenue|\bprofit|\broi|\b\d{2,})", re.I)


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9$%]", "", w.lower())


def classify(word: str, taste_caps: dict) -> str | None:
    """Return a hex color for the word if it's a pain/money word, else None."""
    if not taste_caps.get("highlight", True):
        return None
    n = _norm(word)
    if n in PAIN_WORDS:
        return taste_caps.get("pain_color", "#FF5A00")
    if MONEY_RE.search(word):
        return taste_caps.get("money_color", "#22C55E")
    return None


def apply_case(text: str, case: str) -> str:
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    return text  # "sentence" = leave as spoken


def apply_spelling(text: str, mapping: dict) -> str:
    """Force-correct ASR mishears. Case-insensitive whole-phrase replace."""
    for wrong, right in (mapping or {}).items():
        text = re.sub(rf"\b{re.escape(wrong)}\b", right, text, flags=re.I)
    return text


# ── font resolution (HelveticaNeue collection index quirk) ──
def _font_path_and_index(name_or_path: str, size: int) -> ImageFont.FreeTypeFont:
    candidates = []
    p = Path(name_or_path)
    if p.is_file():
        candidates.append(str(p))
    # friendly names -> macOS files
    named = {
        "helveticaneue-bold": ("/System/Library/Fonts/HelveticaNeue.ttc", "Bold"),
        "helveticaneue": ("/System/Library/Fonts/HelveticaNeue.ttc", "Regular"),
        "helvetica": ("/System/Library/Fonts/Helvetica.ttc", "Bold"),
        "menlo": ("/System/Library/Fonts/Menlo.ttc", "Bold"),
        "menlo-bold": ("/System/Library/Fonts/Menlo.ttc", "Bold"),
        "impact": ("/System/Library/Fonts/Supplemental/Impact.ttf", None),
        "arial": ("/System/Library/Fonts/Supplemental/Arial.ttf", None),
        "arial-bold": ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", None),
    }
    key = name_or_path.lower()
    want_face = None
    if key in named:
        fp, want_face = named[key]
        candidates.append(fp)
    candidates += [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Menlo.ttc",
    ]

    for fp in candidates:
        if not Path(fp).is_file():
            continue
        # .ttc collections: find the face whose name matches what we want (e.g. "Bold").
        if fp.endswith(".ttc"):
            for idx in range(0, 12):
                try:
                    f = ImageFont.truetype(fp, size=size, index=idx)
                    fam, style = f.getname()
                    if want_face is None or want_face.lower() in (style or "").lower():
                        return f
                except Exception:
                    break
            # fallback: first usable index
            try:
                return ImageFont.truetype(fp, size=size, index=0)
            except Exception:
                continue
        try:
            return ImageFont.truetype(fp, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


# ── build caption lines from transcript + EDL (output-timeline offsets) ──
def build_captions(transcript: dict, edl: dict, taste: dict) -> list[dict]:
    """Produce caption line dicts on the OUTPUT timeline (after cuts are applied).

    Each line: {start, end, tokens:[{text,color}], }  — start/end in output seconds.
    Honors video-use Rule 5: output_time = word.start - seg_start + seg_offset.
    """
    caps = taste.get("captions", {})
    if not caps.get("enabled", True):
        return []
    wpl = max(1, int(caps.get("words_per_line", 3)))
    words = transcript.get("words", [])
    ranges = edl.get("ranges", []) if edl else []
    if not ranges:
        dur = transcript.get("duration", 0) or (words[-1]["end"] if words else 0)
        ranges = [{"source_start": 0.0, "source_end": dur, "out_offset": 0.0}]

    lines: list[dict] = []
    for rng in ranges:
        s = float(rng.get("source_start", rng.get("start", 0.0)))
        e = float(rng.get("source_end", rng.get("end", 0.0)))
        off = float(rng.get("out_offset", 0.0))
        seg_words = [w for w in words if w["start"] >= s - 0.01 and w["start"] < e]
        i = 0
        while i < len(seg_words):
            chunk = seg_words[i:i + wpl]
            if not chunk:
                break
            text = " ".join(w["text"] for w in chunk)
            text = apply_spelling(text, caps.get("force_spelling", {}))
            text = apply_case(text, caps.get("case", "upper"))
            toks = []
            for raw in text.split():
                toks.append({"text": raw, "color": classify(raw, caps)})
            start = max(0.0, chunk[0]["start"] - s) + off
            end = max(0.0, chunk[-1]["end"] - s) + off
            lines.append({"start": round(start, 3), "end": round(end, 3), "tokens": toks})
            i += wpl
    return lines


# ── render one caption line to a transparent PNG ──
def render_line_png(line: dict, taste: dict, canvas_w: int, out_png: str) -> tuple[int, int]:
    """Render a caption line, auto-fitting the font so the text always fits the frame width.

    On narrow/portrait clips (Shorts), a 3-word line at full size can be wider than the
    frame. We shrink the font until the line fits within (canvas_w - margin), so captions
    never clip at the edges — a real failure we caught on a 464px-wide portrait clip.
    """
    caps = taste.get("captions", {})
    size = int(caps.get("size", 64))
    text_color = caps.get("text_color", "#FFFFFF")
    outline_color = caps.get("outline_color", "#000000")
    outline = int(caps.get("outline_px", 6))
    tokens = line["tokens"]
    pad = outline + 8
    max_w = max(80, canvas_w - 48)  # leave a side margin

    # shrink font to fit width
    font = _font_path_and_index(caps.get("font", "HelveticaNeue-Bold"), size)
    for _ in range(40):
        space_w = _measure(" ", font)
        widths = [_measure(t["text"], font) for t in tokens]
        total_w = (sum(widths) + space_w * (len(tokens) - 1)) if tokens else 0
        if total_w + pad * 2 <= max_w or size <= 16:
            break
        size = int(size * 0.92)
        font = _font_path_and_index(caps.get("font", "HelveticaNeue-Bold"), size)

    asc, desc = font.getmetrics()
    line_h = asc + desc
    img_w = max(min(canvas_w, total_w + pad * 2), 1)
    img_h = line_h + pad * 2
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = (img_w - total_w) // 2
    y = pad
    for tok, w in zip(tokens, widths):
        col = tok["color"] or text_color
        _draw_outlined(draw, (x, y), tok["text"], font, col, outline_color, outline)
        x += w + space_w
    img.save(out_png)
    return img_w, img_h


def _measure(text: str, font) -> int:
    try:
        box = font.getbbox(text)
        return box[2] - box[0]
    except Exception:
        return int(font.size * 0.6 * len(text))


def _draw_outlined(draw, pos, text, font, fill, outline, w):
    x, y = pos
    if w > 0:
        for dx in range(-w, w + 1):
            for dy in range(-w, w + 1):
                if dx * dx + dy * dy <= w * w:
                    draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


CAPTION_BATCH = 40  # ffmpeg can't take hundreds of PNG inputs in one command (fd / stream limit)


def _caption_y(position: str) -> str:
    if position == "center":
        return "(H-h)/2"
    if position == "upper":
        return "H*0.18"
    return "H*0.74"  # lower — safe above Shorts UI


def _burn_batch(in_mp4: str, lines: list[dict], taste: dict, cw: int, position: str,
                work: Path, out_mp4: str, base_idx: int) -> None:
    """Burn up to CAPTION_BATCH caption lines onto in_mp4 in a single ffmpeg pass."""
    inputs = ["-i", in_mp4]
    filt = []
    cur = "[0:v]"
    for j, line in enumerate(lines):
        png = str(work / f"cap_{base_idx + j:05d}.png")
        render_line_png(line, taste, cw, png)
        inputs += ["-i", png]
        nxt = f"[v{j}]"
        filt.append(
            f"{cur}[{j + 1}:v]overlay=x=(W-w)/2:y={_caption_y(position)}:"
            f"enable='between(t,{line['start']:.3f},{line['end']:.3f})'{nxt}"
        )
        cur = nxt
    cmd = ["ffmpeg", "-y", "-v", "error"] + inputs + [
        "-filter_complex", ";".join(filt),
        "-map", cur, "-map", "0:a?",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", out_mp4,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"caption burn failed:\n{r.stderr[-1500:]}")


# ── burn caption PNGs onto a video (batched so it scales to long videos) ──
def burn(in_mp4: str, lines: list[dict], taste: dict, out_mp4: str) -> None:
    if not lines:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", in_mp4, "-c", "copy", out_mp4], check=True)
        return
    from ve_ffmpeg import probe  # local import to keep module importable standalone

    meta = probe(in_mp4)
    cw = meta["width"]
    position = taste.get("captions", {}).get("position", "lower")
    work = Path(out_mp4).parent / "_caption_pngs"
    work.mkdir(parents=True, exist_ok=True)

    # One pass when small; otherwise batch over intermediates so ffmpeg never sees too many inputs.
    batches = [lines[i:i + CAPTION_BATCH] for i in range(0, len(lines), CAPTION_BATCH)]
    src = in_mp4
    tmps = []
    for bi, batch in enumerate(batches):
        dst = out_mp4 if bi == len(batches) - 1 else str(work / f"_pass_{bi:03d}.mp4")
        _burn_batch(src, batch, taste, cw, position, work, dst, bi * CAPTION_BATCH)
        if bi > 0:
            tmps.append(src)
        src = dst
    for t in tmps:
        Path(t).unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build")
    b.add_argument("transcript")
    b.add_argument("edl")
    b.add_argument("out")
    b.add_argument("--taste", default=None)

    br = sub.add_parser("burn")
    br.add_argument("in_mp4")
    br.add_argument("captions")
    br.add_argument("out_mp4")
    br.add_argument("--taste", default=None)

    st = sub.add_parser("selftest")
    st.add_argument("out_dir")
    st.add_argument("--taste", default=None)

    args = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ve_taste import load_taste

    taste = load_taste(args.taste)

    if args.cmd == "build":
        transcript = json.loads(Path(args.transcript).read_text())
        edl = json.loads(Path(args.edl).read_text()) if Path(args.edl).is_file() else {}
        lines = build_captions(transcript, edl, taste)
        Path(args.out).write_text(json.dumps(lines, indent=2))
        print(f"built {len(lines)} caption lines -> {args.out}")
        return 0

    if args.cmd == "burn":
        lines = json.loads(Path(args.captions).read_text())
        burn(args.in_mp4, lines, taste, args.out_mp4)
        print(f"burned {len(lines)} caption lines -> {args.out_mp4}")
        return 0

    if args.cmd == "selftest":
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        sample = {"start": 0, "end": 2, "tokens": [
            {"text": "WE", "color": None}, {"text": "LOST", "color": taste["captions"]["pain_color"]},
            {"text": "$40K", "color": taste["captions"]["money_color"]},
        ]}
        w, h = render_line_png(sample, taste, 1080, str(out / "caption_sample.png"))
        print(f"rendered caption_sample.png ({w}x{h}) using font={taste['captions']['font']}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
