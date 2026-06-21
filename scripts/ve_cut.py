#!/usr/bin/env python3
"""
ve_cut.py — turn a word-level transcript into an EDL (the cut plan).

Removes filler words ("um, uh, like, you know"), false starts, and long dead-air,
then snaps every cut edge to a word boundary and pads it so words never clip.
All thresholds come from taste.yaml -> cut.* (aggressiveness, fillers, silences, padding).

This produces a plain-English plan you can show the founder BEFORE rendering
(video-use hard rule 11: confirm strategy before execution).

EDL shape (consumed by ve_render.py and ve_captions.py):
  {
    "source": "<abs path>",
    "ranges": [ {"source_start": s, "source_end": e, "out_offset": o, "reason": "..."}... ],
    "removed": [ {"start": s, "end": e, "kind": "filler|silence", "text": "..."} ],
    "kept_s": <float>, "removed_s": <float>, "total_s": <float>
  }

CLI:
  ve_cut.py plan <transcript.json> <out_edl.json> [--taste taste.yaml]
  ve_cut.py summary <edl.json>      # human-readable cut summary
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ve_taste import AGGRESSIVENESS, load_taste  # noqa: E402


def _is_filler(text: str, fillers: list[str]) -> bool:
    n = re.sub(r"[^a-z' ]", "", text.lower()).strip()
    return n in {f.lower() for f in fillers}


def plan_cuts(transcript: dict, taste: dict) -> dict:
    cut = taste.get("cut", {})
    words = transcript.get("words", [])
    duration = float(transcript.get("duration", 0) or (words[-1]["end"] if words else 0))
    if not words:
        return {"source": transcript.get("source"), "ranges": [], "removed": [],
                "kept_s": 0, "removed_s": 0, "total_s": duration}

    agg = AGGRESSIVENESS.get(cut.get("aggressiveness", "medium"), AGGRESSIVENESS["medium"])
    pad = max(0.0, (cut.get("cut_padding_ms", 80) + agg["pad_ms"]) / 1000.0)
    fillers = cut.get("fillers", [])
    remove_filler = cut.get("remove_filler", True)
    sil_thresh = float(cut.get("remove_silences_over_s", 1.2)) * agg["silence_mult"]
    remove_silence = sil_thresh > 0

    remove_stutter = cut.get("remove_stutter", True)

    # 1) mark which words to DROP. Filler words, plus repeated-word stumbles
    #    ("it's it's it's over" -> keep the last "it's"). Keep everything else.
    removed = []
    keep_flags = []
    prev_norm = None
    prev_kept_i = -1
    for i, w in enumerate(words):
        norm = re.sub(r"[^a-z']", "", w["text"].lower())
        drop = remove_filler and _is_filler(w["text"], fillers)
        # stumble: same word as the immediately-preceding KEPT word, within 0.6s -> drop the earlier one
        if (not drop and remove_stutter and norm and norm == prev_norm and len(norm) > 1
                and prev_kept_i >= 0 and (w["start"] - words[prev_kept_i]["end"]) < 0.6):
            keep_flags[prev_kept_i] = False
            removed.append({"start": words[prev_kept_i]["start"], "end": words[prev_kept_i]["end"],
                            "kind": "stutter", "text": words[prev_kept_i]["text"]})
        keep_flags.append(not drop)
        if drop:
            removed.append({"start": w["start"], "end": w["end"], "kind": "filler", "text": w["text"]})
        else:
            prev_norm = norm
            prev_kept_i = i

    # 2) Walk only the KEPT words in order. The gap to the next kept word is the true
    #    silence (it already spans over any dropped filler words in between), so a long
    #    pause hidden behind a filler still gets cut. Split a span when that gap is long.
    kept_idx = [k for k in range(len(words)) if keep_flags[k]]
    spans = []  # (start, end)
    p = 0
    while p < len(kept_idx):
        seg_start = words[kept_idx[p]]["start"]
        prev_end = words[kept_idx[p]]["end"]
        q = p
        while q + 1 < len(kept_idx):
            nxt = words[kept_idx[q + 1]]
            gap = nxt["start"] - prev_end
            if remove_silence and gap > sil_thresh:
                removed.append({"start": prev_end, "end": nxt["start"],
                                "kind": "silence", "text": f"{gap:.1f}s pause"})
                break
            q += 1
            prev_end = words[kept_idx[q]]["end"]
        spans.append((seg_start, prev_end))
        p = q + 1

    # 3) pad each span outward, clamp, merge overlaps that padding created.
    padded = []
    for s, e in spans:
        padded.append((max(0.0, s - pad), min(duration, e + pad)))
    merged = []
    for s, e in sorted(padded):
        if merged and s <= merged[-1][1] + 0.02:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # 4) emit ranges with output-timeline offsets (for caption alignment).
    ranges = []
    out_off = 0.0
    for s, e in merged:
        dur = round(e - s, 3)
        if dur < 0.08:
            continue
        ranges.append({"source_start": round(s, 3), "source_end": round(e, 3),
                       "out_offset": round(out_off, 3), "reason": "kept speech"})
        out_off += dur

    kept = round(sum(r["source_end"] - r["source_start"] for r in ranges), 2)
    return {
        "source": transcript.get("source"),
        "ranges": ranges,
        "removed": removed,
        "kept_s": kept,
        "removed_s": round(max(0.0, duration - kept), 2),
        "total_s": round(duration, 2),
    }


def summarize(edl: dict) -> str:
    fillers = [r for r in edl.get("removed", []) if r["kind"] == "filler"]
    sils = [r for r in edl.get("removed", []) if r["kind"] == "silence"]
    pct = (edl["removed_s"] / edl["total_s"] * 100) if edl.get("total_s") else 0
    lines = [
        f"Cut plan for {Path(str(edl.get('source', 'video'))).name}",
        f"  Original: {edl['total_s']}s  ->  Edited: {edl['kept_s']}s  ({pct:.0f}% removed)",
        f"  Removed {len(fillers)} filler words, {len(sils)} long pauses",
        f"  {len(edl['ranges'])} segments kept",
    ]
    if fillers[:8]:
        shown = ", ".join(f"\"{f['text']}\"" for f in fillers[:8])
        lines.append(f"  Filler examples: {shown}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("transcript")
    p.add_argument("out")
    p.add_argument("--taste", default=None)
    s = sub.add_parser("summary")
    s.add_argument("edl")
    args = ap.parse_args()

    if args.cmd == "plan":
        transcript = json.loads(Path(args.transcript).read_text())
        taste = load_taste(args.taste)
        edl = plan_cuts(transcript, taste)
        Path(args.out).write_text(json.dumps(edl, indent=2))
        print(summarize(edl))
        print(f"-> {args.out}")
        return 0
    if args.cmd == "summary":
        print(summarize(json.loads(Path(args.edl).read_text())))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
