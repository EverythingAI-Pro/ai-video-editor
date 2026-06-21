#!/usr/bin/env python3
"""
ve_broll.py — find + place B-roll ON the words being spoken.

Folds in the b-roll-finder methodology (MIT — see vendor/b-roll-finder/). Two stages:

  PLAN   — read the transcript, classify each candidate moment by route
           (Receipts / Entity / Concept / Cultural-Meme), and propose beats with the
           keyword, the spoken time, and the trusted source to search. Output is a
           plan the agent shows the founder BEFORE fetching anything (taste checkpoint).

  SOURCE — for approved beats, fetch ONE best clip per objective beat, scoped to the
           founder's trusted_sources (never random open YouTube), anchor it +place_lead_s
           after the keyword is spoken, and emit a placement plan ve_render consumes.

Discipline carried from b-roll-finder (so quality stays high):
  - Plan approval IS the pick for objective routes; meme beats are NEVER auto-sourced.
  - Trusted-source-scoped search; open search only as a flagged last resort.
  - No retries: a method that fails once is dropped, not retried.
  - Place ON or just AFTER the word (+lead), never before.
  - allow_memes / allow_ai_broll honored from taste (both default off).

The placement plan shape (consumed by ve_render.overlay_broll):
  { "beats": [ {"start": out_s, "end": out_s, "kind":"video|still",
                "asset": "/abs/path.mp4", "src_in": 0.0, "keyword": "...", "route":"entity",
                "source": "@channel", "reason":"..."} ] }
  Beats with no asset (taste-route / failed source) are kept with asset=null and flagged.

CLI:
  ve_broll.py plan   <transcript.json> <edl.json> <out_plan.json> [--taste taste.yaml]
  ve_broll.py source <plan.json> <assets_dir> <out_plan.json> [--taste taste.yaml]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ve_taste import DENSITY_COVERAGE, load_taste  # noqa: E402

# Phrase-type cues -> route (condensed from b-roll-finder's judgment table).
RECEIPTS_CUES = re.compile(r"\b(people are saying|tweet|posted|headline|study|research shows|"
                           r"review|complaint|stat|\$[\d,]+|\d+%|\d+x)\b", re.I)
ENTITY_CUES = re.compile(r"\b(like|just like|the way|as|when)\s+[A-Z][a-z]+", re.I)
CONCEPT_CUES = re.compile(r"\b(how it works|the process|imagine|think of|funnel|pipeline|"
                          r"step one|step two|framework|system)\b", re.I)


_STOP_CAPS = {"we", "i", "the", "a", "an", "and", "but", "so", "it", "they", "you", "he",
              "she", "this", "that", "our", "my", "your", "their", "just", "like", "when",
              "as", "if", "then", "now", "how", "what", "why", "with", "for", "to"}


def _proper_nouns(text: str) -> list[str]:
    """Capitalized words that aren't sentence-initial filler — the real entities in a line."""
    found = re.findall(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\b", text)
    return [w for w in found if w.split()[0].lower() not in _STOP_CAPS]


def _route_for(text: str) -> tuple[str, str]:
    """Return (route, why). Litmus order: Receipts -> Entity -> Concept."""
    if RECEIPTS_CUES.search(text):
        return "receipts", "time-sensitive claim/stat/quote -> source the receipt"
    pn = _proper_nouns(text)
    if pn:
        return "entity", f"names {pn[0]!r} -> source the canonical clip/footage"
    if CONCEPT_CUES.search(text):
        return "concept", "abstract idea -> on-brand motion graphic / authoritative footage"
    return "skip", "no concrete referent"


def _segments_from_edl(transcript: dict, edl: dict) -> list[dict]:
    """Group kept words into phrase beats on the OUTPUT timeline."""
    words = transcript.get("words", [])
    ranges = edl.get("ranges", []) if edl else []
    if not ranges:
        dur = transcript.get("duration", 0)
        ranges = [{"source_start": 0, "source_end": dur, "out_offset": 0}]
    beats = []
    for rng in ranges:
        s, e, off = rng["source_start"], rng["source_end"], rng.get("out_offset", 0.0)
        seg = [w for w in words if w["start"] >= s - 0.01 and w["start"] < e]
        # break into ~6-12 word phrases on gaps
        cur = []
        for i, w in enumerate(seg):
            cur.append(w)
            gap = (seg[i + 1]["start"] - w["end"]) if i + 1 < len(seg) else 1.0
            if len(cur) >= 6 and (gap > 0.35 or len(cur) >= 12):
                beats.append(_beat(cur, s, off))
                cur = []
        if cur:
            beats.append(_beat(cur, s, off))
    return beats


def _beat(words: list[dict], seg_src_start: float, out_off: float) -> dict:
    text = " ".join(w["text"] for w in words)
    out_start = max(0.0, words[0]["start"] - seg_src_start) + out_off
    out_end = max(0.0, words[-1]["end"] - seg_src_start) + out_off
    return {"text": text, "out_start": round(out_start, 3), "out_end": round(out_end, 3),
            "src_start": words[0]["start"], "keyword": _proper_nouns(text)[:1] or [words[0]["text"]]}


def plan(transcript: dict, edl: dict, taste: dict) -> dict:
    broll = taste.get("broll", {})
    if not broll.get("enabled", True):
        return {"beats": [], "note": "b-roll disabled in taste.yaml"}
    coverage = DENSITY_COVERAGE.get(broll.get("density", "medium"), 0.25)
    lead = float(broll.get("place_lead_s", 0.2))
    allow_memes = broll.get("allow_memes", False)
    trusted = broll.get("trusted_sources", [])
    topic = broll.get("default_topic", "business")
    sources_for_topic = [t for t in trusted if f"[{topic}]" in t] or trusted

    candidates = _segments_from_edl(transcript, edl)
    scored = []
    for b in candidates:
        route, why = _route_for(b["text"])
        if route == "skip":
            continue
        if route == "cultural" and not allow_memes:
            continue
        scored.append({**b, "route": route, "why": why})

    # front-loaded cadence: keep enough beats to hit the coverage target, bias to the intro.
    total_out = max(b["out_end"] for b in candidates) if candidates else 0
    target_count = max(1, int(round(coverage * total_out / 3.0)))  # ~one beat per 3s of coverage
    scored.sort(key=lambda b: (b["route"] != "receipts", b["out_start"]))
    chosen = scored[:max(target_count, min(3, len(scored)))]
    chosen.sort(key=lambda b: b["out_start"])

    beats = []
    for b in chosen:
        kw = b["keyword"][0] if b["keyword"] else ""
        start = round(b["out_start"] + lead, 3)
        end = round(min(b["out_end"] + lead, start + 4.0), 3)  # 2-4s beats
        if end - start < 1.2:
            end = round(start + 1.6, 3)
        suggested = sources_for_topic[:3] if b["route"] == "entity" else []
        beats.append({
            "start": start, "end": end, "keyword": kw, "route": b["route"],
            "reason": b["why"],
            "search": f"{kw} {topic}".strip() if b["route"] == "entity" else None,
            "trusted_sources": suggested,
            "kind": "video", "asset": None, "src_in": 0.0,
            "needs_user": b["route"] in ("cultural",),
        })
    return {"beats": beats, "coverage_target": coverage, "topic": topic,
            "note": "objective beats (receipts/entity/concept) will be sourced; "
                    "meme/taste beats need your library."}


def _yt_search_in_channel(handle: str, query: str, n: int = 3) -> list[dict]:
    """Channel-scoped search (the default for Entity beats). Metadata only."""
    url = f"https://www.youtube.com/{handle}/search?query={query.replace(' ', '+')}"
    r = subprocess.run(["yt-dlp", url, "--flat-playlist", "--playlist-end", str(n),
                        "--print", "%(title).80s|||%(duration)s|||%(id)s"],
                       capture_output=True, text=True)
    out = []
    for line in r.stdout.strip().splitlines():
        parts = line.split("|||")
        if len(parts) == 3 and parts[2]:
            out.append({"title": parts[0], "duration": parts[1], "id": parts[2]})
    return out


def source(plan_obj: dict, assets_dir: Path, taste: dict) -> dict:
    """Fetch one clip per objective beat (no retries; trusted-scoped). Honors allow_ai_broll."""
    broll = taste.get("broll", {})
    assets_dir.mkdir(parents=True, exist_ok=True)
    out_beats = []
    for i, b in enumerate(plan_obj.get("beats", [])):
        if b.get("needs_user") or b["route"] == "cultural":
            b["flag"] = "supply from your library (taste route)"
            out_beats.append(b)
            continue
        handle = None
        for src in b.get("trusted_sources", []):
            m = re.match(r"\s*(@[\w.-]+)", src)
            if m:
                handle = m.group(1)
                break
        if not handle or not b.get("search"):
            b["flag"] = "no trusted source matched — drop or supply manually"
            out_beats.append(b)
            continue
        hits = _yt_search_in_channel(handle, b["search"], n=3)
        if not hits:
            b["flag"] = f"no result in {handle} (not retried) — dropped"
            out_beats.append(b)
            continue
        vid = hits[0]["id"]
        dst = assets_dir / f"beat_{i:03d}.mp4"
        dl = subprocess.run(
            ["yt-dlp", f"https://www.youtube.com/watch?v={vid}",
             "-f", "bv*[height<=1080]+ba/b", "--download-sections", "*0-8",
             "-o", str(dst.with_suffix(".%(ext)s")), "--force-overwrites"],
            capture_output=True, text=True)
        found = next(iter(sorted(assets_dir.glob(f"beat_{i:03d}.*"))), None)
        if dl.returncode != 0 or not found:
            b["flag"] = f"download failed in {handle} (not retried) — dropped"
            out_beats.append(b)
            continue
        b["asset"] = str(found)
        b["source"] = handle
        b["src_in"] = 0.5  # skip a beat of intro
        out_beats.append(b)
    placed = sum(1 for b in out_beats if b.get("asset"))
    return {"beats": out_beats, "placed": placed, "total": len(out_beats)}


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("transcript")
    p.add_argument("edl")
    p.add_argument("out")
    p.add_argument("--taste", default=None)
    s = sub.add_parser("source")
    s.add_argument("plan")
    s.add_argument("assets_dir")
    s.add_argument("out")
    s.add_argument("--taste", default=None)
    args = ap.parse_args()
    taste = load_taste(args.taste)

    if args.cmd == "plan":
        transcript = json.loads(Path(args.transcript).read_text())
        edl = json.loads(Path(args.edl).read_text()) if Path(args.edl).is_file() else {}
        res = plan(transcript, edl, taste)
        Path(args.out).write_text(json.dumps(res, indent=2))
        print(f"planned {len(res['beats'])} b-roll beats -> {args.out}")
        for b in res["beats"]:
            tag = "(needs your library)" if b.get("needs_user") else f"search {b.get('search')!r}"
            print(f"  [{b['start']:.1f}-{b['end']:.1f}s] {b['route']:8s} {b['keyword']!r:20s} {tag}")
        return 0
    if args.cmd == "source":
        plan_obj = json.loads(Path(args.plan).read_text())
        res = source(plan_obj, Path(args.assets_dir), taste)
        Path(args.out).write_text(json.dumps(res, indent=2))
        print(f"sourced {res['placed']}/{res['total']} beats -> {args.out}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
