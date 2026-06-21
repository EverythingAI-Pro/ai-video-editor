#!/usr/bin/env python3
"""
ve_virality.py — score moments by DELIVERY, not just words. The "find the viral spot" engine.

The transcript-only hook rubric (ve_shorts.py) reads what's said but can't hear HOW it's said —
so it misses energy spikes and gets fooled by keyword-matching filler. This adds the missing
signal: it listens to the audio.

For each candidate window it computes:
  - energy_peak    : loudest RMS moment vs the video's baseline (emphasis / excitement)
  - energy_var     : dynamic range inside the window (flat monotone = low; animated = high)
  - laughter       : Scribe audio-events ((laughs)/(laughter)/(applause)) inside the window
  - pause_drama    : a meaningful pause right BEFORE the window's punch (setup → payoff)
  - hook_score     : the existing 8-pattern transcript rubric (so words still count)

Final `viral_score` blends them. Audio is what separates a real punchline from a keyword match.

CLI:
  ve_virality.py score <video> <transcript.json> <out.json> [--taste taste.yaml] [--top N]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ve_shorts import _windows, score_window  # reuse the transcript rubric  # noqa: E402
from ve_taste import load_taste  # noqa: E402

LAUGH_EVENTS = ("laugh", "laughter", "applause", "cheer", "gasp")


def _audio_envelope(video: str, hz: int = 50) -> tuple[np.ndarray, int]:
    """Return an RMS-energy envelope sampled at `hz` samples/sec, plus that rate.

    We pull mono 16k audio via ffmpeg, then compute RMS over fixed frames. Cheap and robust.
    """
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav = tf.name
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", video, "-vn", "-ac", "1",
                    "-ar", "16000", "-c:a", "pcm_s16le", wav], check=True)
    with wave.open(wav, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    Path(wav).unlink(missing_ok=True)
    if not raw:
        return np.zeros(1), hz
    sig = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    frame = max(1, sr // hz)
    nf = len(sig) // frame
    if nf == 0:
        return np.array([np.sqrt(np.mean(sig ** 2)) if len(sig) else 0.0]), hz
    sig = sig[: nf * frame].reshape(nf, frame)
    rms = np.sqrt(np.mean(sig ** 2, axis=1) + 1e-9)
    return rms, hz


def _window_energy(env: np.ndarray, hz: int, start: float, end: float,
                   base_mean: float, base_std: float) -> dict:
    a, b = int(start * hz), int(end * hz)
    seg = env[a:b]
    if seg.size == 0:
        return {"energy_peak": 0.0, "energy_var": 0.0}
    # peak emphasis = how many std-devs above baseline the loudest moment reaches
    peak_z = (seg.max() - base_mean) / (base_std + 1e-6)
    var = float(seg.std() / (base_mean + 1e-6))  # relative dynamic range
    return {"energy_peak": float(peak_z), "energy_var": var}


def _pause_before(env: np.ndarray, hz: int, start: float, base_mean: float) -> float:
    """A quiet stretch right before the window = dramatic setup. Returns seconds of low energy."""
    look = int(0.8 * hz)
    a = max(0, int(start * hz) - look)
    pre = env[a:int(start * hz)]
    if pre.size == 0:
        return 0.0
    quiet = pre < (base_mean * 0.5)
    # length of the trailing quiet run
    run = 0
    for v in quiet[::-1]:
        if v:
            run += 1
        else:
            break
    return run / hz


def _stutter_ratio(words: list[dict]) -> float:
    """Fraction of word-to-word transitions that are a repeat ('it's it's it's') — messy delivery."""
    toks = [w["text"].lower().strip(".,!?") for w in words]
    if len(toks) < 4:
        return 0.0
    reps = sum(1 for i in range(1, len(toks)) if toks[i] and toks[i] == toks[i - 1])
    return round(reps / max(1, len(toks) - 1), 3)


def _laughter(events: list[dict], start: float, end: float) -> int:
    c = 0
    for e in events:
        t = e.get("start", 0)
        if start <= t <= end and any(k in e.get("text", "").lower() for k in LAUGH_EVENTS):
            c += 1
    return c


def _llm_rank(clips: list[dict], want: int) -> list[dict] | None:
    """Ask an LLM to pick the genuinely viral finalists from the audio-scored shortlist.

    Audio energy can't tell an excited STUMBLE from an excited PUNCHLINE — a human (or LLM)
    reading the line can. We send the shortlist's transcripts and ask for a ranked pick with
    a reason, and a reject flag for stumbles / mid-thoughts / no-payoff clips.

    Uses the `claude` CLI if present (what FounderOS users have); falls back to the Anthropic
    API if ANTHROPIC_API_KEY is set; returns None if neither is available (caller keeps audio order).
    """
    import os
    import shutil

    if not clips:
        return None
    shortlist = [{
        "i": i,
        "text": c.get("text_full", c["transcript_excerpt"]),
        "stutter": c.get("stutter", 0.0),  # >0.05 means audible repeated-word stumbles
    } for i, c in enumerate(clips)]
    prompt = (
        "You are a viral short-form video editor. Below are FULL candidate clips from a longer "
        "video, already pre-filtered for audio energy. Each has a `stutter` score (fraction of "
        "repeated-word stumbles; >0.05 = the delivery is messy/stumbly even if the words are good). "
        "Pick the clips that would actually perform as a standalone Short/Reel/TikTok. REJECT clips "
        "that are mid-thought, a stumble/stutter (high stutter score), pure setup with no payoff, or "
        "need outside context. A great clip has a hook in its first sentence and a clear payoff, and "
        "is delivered cleanly. Return STRICT JSON only: "
        '{"ranked":[{"i":<index>,"reason":"<8 words>","reject":false}, ...]} '
        f"ordered best-first, rejects last with reject:true. Pick up to {want} clean keepers.\n\n"
        + json.dumps(shortlist, ensure_ascii=False)
    )

    raw = None
    claude_bin = shutil.which("claude")
    if claude_bin:
        try:
            r = subprocess.run([claude_bin, "-p", prompt], capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                raw = r.stdout
        except Exception:
            raw = None
    if raw is None and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import requests

            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                         "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-6", "max_tokens": 1024,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=120,
            )
            if resp.ok:
                raw = "".join(b.get("text", "") for b in resp.json().get("content", []))
        except Exception:
            raw = None
    if not raw:
        return None
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        data = json.loads(raw[start:end])
        ranked = data.get("ranked", [])
    except Exception:
        return None

    out = []
    for entry in ranked:
        idx = entry.get("i")
        if not isinstance(idx, int) or idx < 0 or idx >= len(clips):
            continue
        c = dict(clips[idx])
        c["llm_reason"] = entry.get("reason", "")
        c["llm_reject"] = bool(entry.get("reject", False))
        out.append(c)
    keepers = [c for c in out if not c.get("llm_reject")]
    return keepers or None


def score_virality(video: str, transcript: dict, taste: dict, top: int = 8,
                   use_llm: bool = True) -> dict:
    sh = taste.get("shorts", {})
    lo, hi = (list(sh.get("length_s", [15, 60])) + [60])[:2]
    lo, hi = float(lo), float(hi)
    events = transcript.get("events", [])

    env, hz = _audio_envelope(video)
    base_mean = float(np.mean(env)) if env.size else 0.0
    base_std = float(np.std(env)) if env.size else 1.0

    scored = []
    for start, end, words in _windows(transcript, lo, hi):
        hook, hits = score_window(words)
        en = _window_energy(env, hz, start, end, base_mean, base_std)
        laughs = _laughter(events, start, end)
        pause = _pause_before(env, hz, start, base_mean)
        text = " ".join(w["text"] for w in words)

        # blend — audio carries real weight so delivery can rescue or sink a clip
        audio_pts = (
            min(en["energy_peak"], 4.0) * 6.0          # emphasis spikes (up to ~24)
            + min(en["energy_var"], 1.5) * 8.0         # animated vs monotone (up to ~12)
            + laughs * 12.0                            # laughter is gold
            + min(pause, 1.0) * 8.0                    # a beat before the punch
        )
        viral = round(hook + audio_pts, 1)
        scored.append({
            "source_start": round(start, 2), "source_end": round(end, 2),
            "viral_score": viral, "hook_score": hook, "hook_patterns": hits,
            "audio": {"energy_peak_z": round(en["energy_peak"], 2),
                      "energy_var": round(en["energy_var"], 2),
                      "laughs": laughs, "pause_before_s": round(pause, 2)},
            "stutter": _stutter_ratio(words),       # 0..1 — fraction of repeated-word stumbles
            "text_full": text,                      # FULL clip text (the LLM reads this, not a snippet)
            "transcript_excerpt": text[:240],
        })

    scored.sort(key=lambda x: x["viral_score"], reverse=True)
    # non-overlapping audio-scored shortlist (take a few extra to give the LLM room to reject)
    shortlist = []
    for c in scored:
        if any(not (c["source_end"] <= p["source_start"] or c["source_start"] >= p["source_end"])
               for p in shortlist):
            continue
        shortlist.append(c)
        if len(shortlist) >= max(top + 4, 8):
            break

    ranker = "audio-only"
    picked = shortlist[:top]
    if use_llm:
        llm = _llm_rank(shortlist, top)
        if llm:
            picked = llm[:top]
            ranker = "audio+llm"

    for i, p in enumerate(picked):
        p["rank"] = i
        p["edl"] = {"ranges": [{"source_start": p["source_start"],
                                "source_end": p["source_end"], "out_offset": 0.0}]}
    return {"clips": picked, "scanned": len(scored), "ranker": ranker,
            "baseline": {"mean": round(base_mean, 5), "std": round(base_std, 5)}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("transcript")
    ap.add_argument("out")
    ap.add_argument("--taste", default=None)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM finalist ranker (audio-only)")
    args = ap.parse_args()
    taste = load_taste(args.taste)
    transcript = json.loads(Path(args.transcript).read_text())
    res = score_virality(args.video, transcript, taste, args.top, use_llm=not args.no_llm)
    Path(args.out).write_text(json.dumps(res, indent=2))
    print(f"virality [{res['ranker']}]: scanned {res['scanned']} windows, "
          f"top {len(res['clips'])} clips -> {args.out}")
    for c in res["clips"]:
        a = c["audio"]
        reason = f"  ← {c['llm_reason']}" if c.get("llm_reason") else ""
        print(f"  #{c['rank']} [{c['source_start']}-{c['source_end']}s] viral={c['viral_score']} "
              f"(hook={c['hook_score']} energy={a['energy_peak_z']}z laughs={a['laughs']} "
              f"pause={a['pause_before_s']}s){reason}")
        print(f"      {c['transcript_excerpt'][:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
