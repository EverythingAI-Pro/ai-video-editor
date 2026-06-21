#!/usr/bin/env python3
"""
ve_shorts.py — pull vertical Shorts out of a long video, scored by a viral-hook rubric.

Folds in the podcast-ad-clipper hook rubric. Reads the transcript, scores every
candidate window on 8 viral patterns + standalone-ness, picks the top N that clear
taste.shorts.min_hook_score, and emits per-short EDLs (each a sub-edit ve_render can
build into a 9:16 captioned clip).

8 hook patterns (each adds to the score):
  curiosity gap · named enemy · surprising specific · contrarian take ·
  vulnerable confession · aspirational reveal · ICP call-out · taboo truth

CLI:
  ve_shorts.py find <transcript.json> <out_shorts.json> [--taste taste.yaml]
  ve_shorts.py script <transcript.json> [--taste taste.yaml]   # caption/title script for each short
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ve_taste import load_taste  # noqa: E402

PATTERNS = {
    "curiosity_gap": re.compile(r"\b(reason|secret|nobody tells you|here's why|the truth|what (no|few))\b", re.I),
    "named_enemy": re.compile(r"\b(unlike|instead of|stop using|vs\.?|better than|kill|replace|"
                              r"per[- ]seat|expensive tools?)\b", re.I),
    "surprising_specific": re.compile(r"(\$[\d,]+|\d{2,}[kmKM]?\b|\d+%|\d+x|\d+ a day)", re.I),
    "contrarian": re.compile(r"\b(everyone (thinks|says)|conventional|actually|wrong|myth|opposite)\b", re.I),
    "confession": re.compile(r"\b(I failed|we lost|my mistake|I was wrong|honestly|struggled|scared|"
                             r"almost (quit|gave up))\b", re.I),
    "aspirational": re.compile(r"\b(scaled|freedom|hours? back|on autopilot|while (I|you) sleep|"
                               r"million|7[- ]figure|dream)\b", re.I),
    "icp_callout": re.compile(r"\b(if you('?re| are)|for (founders|agencies|owners|coaches|sales teams)|"
                              r"when you run)\b", re.I),
    "taboo": re.compile(r"\b(nobody (talks|says)|not supposed to|dirty secret|they don't want)\b", re.I),
}


def _windows(transcript: dict, lo: float, hi: float):
    """Yield candidate (start,end,words) windows of lo..hi seconds, sliding on sentence ends."""
    words = transcript.get("words", [])
    if not words:
        return
    # sentence boundaries: word ends with . ! ?
    bounds = [0] + [i + 1 for i, w in enumerate(words) if re.search(r"[.!?]$", w["text"])]
    bounds = sorted(set(bounds + [len(words)]))
    for a_idx in range(len(bounds) - 1):
        i = bounds[a_idx]
        for b_idx in range(a_idx + 1, len(bounds)):
            j = bounds[b_idx]
            if j <= i:
                continue
            start = words[i]["start"]
            end = words[j - 1]["end"]
            dur = end - start
            if dur < lo:
                continue
            if dur > hi:
                break
            yield start, end, words[i:j]


def score_window(words: list[dict]) -> tuple[int, list[str]]:
    text = " ".join(w["text"] for w in words)
    hits = []
    score = 0
    for name, rx in PATTERNS.items():
        if rx.search(text):
            score += 9
            hits.append(name)
    # standalone-ness: first word shouldn't be a dangling connector
    if re.match(r"^(and|but|so|because|which|that)\b", text, re.I):
        score -= 6
    # quotability: a strong number or a punchy short first sentence
    if PATTERNS["surprising_specific"].search(text):
        score += 4
    return max(0, score), hits


def find_shorts(transcript: dict, taste: dict) -> dict:
    sh = taste.get("shorts", {})
    lo, hi = (sh.get("length_s", [20, 60]) + [60])[:2]
    lo, hi = float(lo), float(hi)
    min_score = int(sh.get("min_hook_score", 55))
    want = int(sh.get("count", 3))

    scored = []
    for start, end, words in _windows(transcript, lo, hi):
        s, hits = score_window(words)
        scored.append({"start": round(start, 2), "end": round(end, 2),
                       "score": s, "hits": hits,
                       "text": " ".join(w["text"] for w in words)})
    scored.sort(key=lambda x: x["score"], reverse=True)

    # pick top, non-overlapping
    picked = []
    for c in scored:
        if c["score"] < min_score:
            continue
        if any(not (c["end"] <= p["start"] or c["start"] >= p["end"]) for p in picked):
            continue
        picked.append(c)
        if len(picked) >= want:
            break

    note = None
    if len(picked) < want:
        note = (f"Only {len(picked)} clip(s) scored >= {min_score}. Lower shorts.min_hook_score "
                f"in taste.yaml to surface more (they'll be weaker hooks).")
    shorts = []
    for idx, p in enumerate(picked):
        shorts.append({
            "index": idx,
            "source_start": p["start"], "source_end": p["end"],
            "hook_score": p["score"], "hook_patterns": p["hits"],
            "aspect": sh.get("aspect", "9:16"),
            "transcript_excerpt": p["text"][:240],
            # a per-short EDL so ve_render can build it directly
            "edl": {"ranges": [{"source_start": p["start"], "source_end": p["end"], "out_offset": 0.0}]},
        })
    return {"shorts": shorts, "note": note, "scanned": len(scored)}


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("transcript")
    f.add_argument("out")
    f.add_argument("--taste", default=None)
    sc = sub.add_parser("script")
    sc.add_argument("transcript")
    sc.add_argument("--taste", default=None)
    args = ap.parse_args()
    taste = load_taste(args.taste)
    transcript = json.loads(Path(args.transcript).read_text())

    if args.cmd == "find":
        res = find_shorts(transcript, taste)
        Path(args.out).write_text(json.dumps(res, indent=2))
        print(f"found {len(res['shorts'])} shorts (scanned {res['scanned']} windows) -> {args.out}")
        for s in res["shorts"]:
            print(f"  #{s['index']} [{s['source_start']}-{s['source_end']}s] "
                  f"score={s['hook_score']} {s['hook_patterns']}")
        if res.get("note"):
            print(f"  note: {res['note']}")
        return 0
    if args.cmd == "script":
        res = find_shorts(transcript, taste)
        for s in res["shorts"]:
            print(f"\n=== SHORT #{s['index']}  (hook {s['hook_score']}: {', '.join(s['hook_patterns'])}) ===")
            print(f"Clip: {s['source_start']}-{s['source_end']}s")
            print(f"Hook line (first 12 words): {' '.join(s['transcript_excerpt'].split()[:12])}…")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
