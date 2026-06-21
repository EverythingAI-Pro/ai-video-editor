#!/usr/bin/env python3
"""
ve_postpack.py — turn an edit into ready-to-post captions, hooks, and hashtags per platform.

This is the "help them post it" layer. Given the transcript + the shorts found, it writes a
post pack: for the main video and each Short, a platform-tuned caption, a scroll-stopping hook,
and a hashtag set for TikTok / Reels (Instagram) / YouTube Shorts / X / LinkedIn.

It does NOT call an LLM (so it works offline, with no keys). It builds posts from the actual
spoken words + the hook patterns ve_shorts already detected, using platform-aware templates and
length limits. The agent can always rewrite any line — this gives a strong, honest first draft.

CLI:
  ve_postpack.py build <transcript.json> <shorts.json> <out_postpack.json> [--taste taste.yaml]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ve_taste import load_taste  # noqa: E402

# Platform limits + style. caption_max is a soft cap we trim to.
PLATFORMS = {
    "tiktok": {"name": "TikTok", "caption_max": 150, "tags": 5, "style": "punchy, lowercase ok, 1 emoji"},
    "reels": {"name": "Instagram Reels", "caption_max": 180, "tags": 8, "style": "hook + value + CTA"},
    "shorts": {"name": "YouTube Shorts", "caption_max": 100, "tags": 4, "style": "searchable title-ish"},
    "x": {"name": "X (Twitter)", "caption_max": 240, "tags": 2, "style": "thread-starter, no fluff"},
    "linkedin": {"name": "LinkedIn", "caption_max": 220, "tags": 3, "style": "professional, insight-led"},
}

# generic, evergreen hashtags by vibe; the founder swaps these for their niche in taste.yaml later.
BASE_TAGS = ["#founder", "#startup", "#AI", "#automation", "#marketing", "#sales", "#smallbusiness",
             "#entrepreneur", "#contentcreation", "#growth", "#businesstips", "#productivity"]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _first_sentence(text: str) -> str:
    m = re.split(r"(?<=[.!?])\s+", text.strip())
    return m[0] if m else text.strip()


def _hook_from(text: str, patterns: list[str]) -> str:
    """Build a scroll-stopping hook line from the clip's first sentence + its detected pattern."""
    s = _first_sentence(text)
    s = s.rstrip(".")
    # light pattern dressing
    if "curiosity_gap" in patterns:
        return s if "?" in s else s + " (here's why)"
    if "named_enemy" in patterns:
        return s
    if "surprising_specific" in patterns:
        return s
    if "confession" in patterns:
        return s
    if "icp_callout" in patterns:
        return s
    return s


def _caption(text: str, platform: str, hook: str, limit: int) -> str:
    body = _clean(text)
    if platform == "x":
        cap = hook
    elif platform == "shorts":
        cap = hook  # title-ish
    elif platform == "linkedin":
        first = _first_sentence(body)
        # avoid repeating the hook when it IS the first sentence
        cap = hook if _clean(first).rstrip(".!?") in _clean(hook) else f"{hook}\n\n{first}"
    else:  # tiktok / reels
        cap = f"{hook}"
    cap = _clean(cap)
    if len(cap) > limit:
        cap = cap[: limit - 1].rstrip() + "…"
    return cap


def _tags(n: int, extra: list[str]) -> list[str]:
    out = []
    for t in (extra + BASE_TAGS):
        t = t if t.startswith("#") else "#" + re.sub(r"[^a-zA-Z0-9]", "", t)
        if t.lower() not in [x.lower() for x in out]:
            out.append(t)
        if len(out) >= n:
            break
    return out


def build_pack(transcript: dict, shorts: dict, taste: dict) -> dict:
    brand = taste.get("brand", {})
    niche_tags = []  # founders can extend taste later; keep generic + honest for now
    items = []

    # one "post" per short, plus a main-video post.
    # accepts both the virality engine output ({"clips":[...]}) and the rubric output ({"shorts":[...]}).
    main_text = " ".join(w["text"] for w in transcript.get("words", []))[:600]
    sources = [{"id": "main", "label": "Main video", "text": main_text, "patterns": []}]
    short_list = shorts.get("clips") or shorts.get("shorts") or []
    for i, s in enumerate(short_list):
        idx = s.get("rank", s.get("index", i))
        score = s.get("viral_score", s.get("hook_score", 0))
        sources.append({
            "id": f"short{idx}",
            "label": f"Short #{idx} ({score} score)",
            "text": s.get("text_full") or s.get("transcript_excerpt", ""),
            "patterns": s.get("hook_patterns", []),
        })

    for src in sources:
        hook = _hook_from(src["text"], src["patterns"])
        per_platform = {}
        for key, meta in PLATFORMS.items():
            per_platform[key] = {
                "platform": meta["name"],
                "hook": hook,
                "caption": _caption(src["text"], key, hook, meta["caption_max"]),
                "hashtags": _tags(meta["tags"], niche_tags),
            }
        items.append({"id": src["id"], "label": src["label"], "hook": hook, "platforms": per_platform})

    return {"items": items, "note": "First-draft captions from your real words + detected hooks. "
                                    "Edit any line; add your niche hashtags in taste.yaml later."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("shorts")
    ap.add_argument("out")
    ap.add_argument("--taste", default=None)
    args = ap.parse_args()
    taste = load_taste(args.taste)
    transcript = json.loads(Path(args.transcript).read_text())
    shorts = json.loads(Path(args.shorts).read_text()) if Path(args.shorts).is_file() else {"shorts": []}
    pack = build_pack(transcript, shorts, taste)
    Path(args.out).write_text(json.dumps(pack, indent=2))
    print(f"post pack: {len(pack['items'])} posts across {len(PLATFORMS)} platforms -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
