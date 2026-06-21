#!/usr/bin/env python3
"""
ve_taste.py — load the founder's taste.yaml (the ONE customization layer).

Why hand-rolled YAML: this skill ships self-contained and we don't want to force a
PyYAML install on a founder. taste.yaml uses a small, predictable subset (scalars,
nested maps, simple lists, inline lists), so a tolerant mini-parser is enough and
keeps the dependency surface tiny. If PyYAML happens to be installed we use it.

Public API:
    load_taste(path=None) -> dict      # merged over DEFAULTS, so missing keys never crash a job
    DEFAULTS                           # the baseline taste, also the documentation of every key
"""
from __future__ import annotations

import os
from pathlib import Path

# The baseline. taste.yaml only needs to override what the founder cares about;
# anything they delete falls back to here, so an edit job can never crash on a missing key.
DEFAULTS: dict = {
    "confirmed_by": "",
    "captions": {
        "enabled": True,
        "font": "HelveticaNeue-Bold",
        "size": 64,
        "case": "upper",
        "words_per_line": 3,
        "position": "lower",
        "text_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_px": 6,
        "highlight": True,
        "pain_color": "#FF5A00",
        "money_color": "#22C55E",
        "force_spelling": {
            "ris dial": "RizzDial",
            "risdial": "RizzDial",
            "rizz dial": "RizzDial",
            "school": "Skool",
        },
    },
    "cut": {
        "remove_filler": True,
        "fillers": ["um", "uh", "uhh", "erm", "like", "you know", "i mean", "sort of", "kind of", "basically"],
        "aggressiveness": "medium",
        "remove_silences_over_s": 1.2,
        "cut_padding_ms": 80,
        "keep_peaks": True,
    },
    "color": {"grade": "auto", "raw_filter": ""},
    "brand": {
        "primary_color": "#FF5A00",
        "accent_color": "#22C55E",
        "logo_path": "",
        "watermark": False,
        "watermark_corner": "br",
        "endcard": {"enabled": False, "seconds": 3, "headline": "", "background": "#0A0A0A"},
    },
    "broll": {
        "enabled": True,
        "audio": "silent",
        "density": "medium",
        "place_lead_s": 0.2,
        "stills_zoom": True,
        "stills_zoom_rate": 0.015,
        "allow_memes": False,
        "allow_ai_broll": False,
        "attribution": "auto",
        "trusted_sources": [],
        "default_topic": "business",
    },
    "shorts": {
        "enabled": True,
        "count": 3,
        "aspect": "9:16",
        "length_s": [20, 60],
        "min_hook_score": 55,
    },
    "output": {"resolution": "match", "fps": "match", "loudness_lufs": -14},
}

# Density label -> rough b-roll coverage fraction (used by find_broll planning).
DENSITY_COVERAGE = {"light": 0.10, "medium": 0.25, "heavy": 0.50}
# Cut aggressiveness -> (silence multiplier, extra padding ms). Tighter = shorter pauses kept.
AGGRESSIVENESS = {
    "gentle": {"silence_mult": 1.6, "pad_ms": 40},
    "medium": {"silence_mult": 1.0, "pad_ms": 0},
    "tight": {"silence_mult": 0.55, "pad_ms": -20},
}


def _coerce(raw: str):
    """Turn a YAML scalar string into a python value."""
    s = raw.strip()
    if s.startswith(("'", '"')) and s.endswith(("'", '"')) and len(s) >= 2:
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", "none", ""):
        return None
    # inline list [a, b, c]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_coerce(part) for part in _split_top(inner)]
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _split_top(inner: str) -> list[str]:
    """Split a flat inline list on commas (no nested structures expected in taste.yaml)."""
    out, buf, depth, quote = [], "", 0, ""
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
            buf += ch
        elif ch in "[{":
            depth += 1
            buf += ch
        elif ch in "]}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf)
    return out


def _mini_yaml(text: str) -> dict:
    """Tolerant parser for the taste.yaml subset: nested maps, '- ' lists, 'k: v' pairs.

    Indentation-driven. Good enough for our own config; not a general YAML engine.
    """
    root: dict = {}
    # stack of (indent, container). container is the dict/list we're appending into.
    stack: list[tuple[int, object]] = [(-1, root)]
    pending_key: list[tuple[int, str, dict]] = []  # (indent, key, parent) awaiting a nested block

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        # strip trailing comments (only when not inside quotes — cheap heuristic)
        if "#" in raw:
            in_q = ""
            cut_at = None
            for idx, ch in enumerate(raw):
                if in_q:
                    if ch == in_q:
                        in_q = ""
                elif ch in "'\"":
                    in_q = ch
                elif ch == "#":
                    cut_at = idx
                    break
            if cut_at is not None:
                raw = raw[:cut_at]
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()

        # unwind to the parent at this indent
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            stack = [(-1, root)]
        parent = stack[-1][1]

        if stripped.startswith("- "):
            item = stripped[2:].strip()
            if not isinstance(parent, list):
                continue
            parent.append(_coerce(item))
            continue

        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip().strip("'\"")
        val = val.strip()

        if val == "":
            # nested block follows — peek to decide dict vs list
            nxt_indent = None
            is_list = False
            for j in range(i, len(lines)):
                peek = lines[j]
                if not peek.strip() or peek.strip().startswith("#"):
                    continue
                nxt_indent = len(peek) - len(peek.lstrip(" "))
                is_list = peek.strip().startswith("- ")
                break
            container: object = [] if (nxt_indent is not None and nxt_indent > indent and is_list) else {}
            if isinstance(parent, dict):
                parent[key] = container
            stack.append((indent, container))
        else:
            if isinstance(parent, dict):
                parent[key] = _coerce(val)
    return root


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def find_taste_path(start: str | None = None) -> Path | None:
    """taste.yaml lives next to this skill by default; a project copy overrides it."""
    here = Path(__file__).resolve().parent.parent  # skill root
    candidates = []
    if start:
        p = Path(start)
        candidates += [p / "taste.yaml", p]
    candidates += [here / "taste.yaml"]
    for c in candidates:
        if c.is_file() and c.name == "taste.yaml":
            return c
        if c.is_dir() and (c / "taste.yaml").is_file():
            return c / "taste.yaml"
    return None


def load_taste(path: str | None = None) -> dict:
    """Load taste, merged over DEFAULTS so every key is always present."""
    p = Path(path) if path and Path(path).is_file() else find_taste_path(path)
    if not p or not p.is_file():
        return _deep_merge(DEFAULTS, {})
    text = p.read_text(encoding="utf-8")
    parsed: dict
    try:
        import yaml  # type: ignore

        parsed = yaml.safe_load(text) or {}
    except Exception:
        parsed = _mini_yaml(text)
    return _deep_merge(DEFAULTS, parsed)


if __name__ == "__main__":
    import json
    import sys

    t = load_taste(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(t, indent=2))
