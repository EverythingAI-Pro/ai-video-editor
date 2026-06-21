#!/usr/bin/env python3
"""
ve_transcribe.py — word-level transcript for an edit.

Order of preference (every cut/caption/b-roll decision needs WORD-LEVEL timing):
  1. ElevenLabs Scribe (scribe_v1, word granularity, diarization, audio events)  ← best, needs ELEVENLABS_API_KEY
  2. yt-dlp auto/uploaded captions, when the source is a URL                       ← free, no key (line-level only)
  3. a transcript JSON the caller already has (pass --from-json)

Output: <edit_dir>/transcripts/<stem>.json  in a normalized shape the rest of the
skill reads, regardless of which engine produced it:

  {
    "source": "<abs path or url>",
    "engine": "scribe" | "captions",
    "words": [ {"text": "hello", "start": 0.42, "end": 0.71, "speaker": "speaker_0"}, ... ],
    "events": [ {"text": "(laughs)", "start": .., "end": ..} ],   # scribe only
    "duration": 87.4
  }

Caching: per source stem; never re-transcribe unless the source changed (video-use hard rule 9).

Keys are read from the environment, or from a .env next to this skill, or from the
video-use skill's .env (so a founder who already set it up there doesn't re-enter it).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _load_env_keys() -> None:
    """Populate os.environ from likely .env locations without overwriting real env vars."""
    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / ".env",
        here.parent.parent.parent.parent.parent.parent / "skills" / "video-use" / ".env",  # workspace/skills/video-use
        Path.home() / "My WorkSpace" / "skills" / "video-use" / ".env",
    ]
    for c in candidates:
        try:
            if not c.is_file():
                continue
            for line in c.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


def probe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def extract_audio(video: str, wav: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", wav],
        check=True,
    )


def transcribe_scribe(video: str, edit_dir: Path, language: str | None, num_speakers: int | None) -> dict:
    import requests  # provided by the skill venv

    api_key = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_API_KEY")
    if not api_key:
        raise RuntimeError("no ELEVENLABS_API_KEY")

    tmp = edit_dir / "transcripts"
    tmp.mkdir(parents=True, exist_ok=True)
    wav = tmp / (Path(video).stem + ".wav")
    extract_audio(video, str(wav))

    data = {
        "model_id": "scribe_v1",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    if language:
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)

    with open(wav, "rb") as f:
        resp = requests.post(
            "https://api.elevenlabs.io/v1/speech-to-text",
            headers={"xi-api-key": api_key},
            files={"file": (wav.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )
    wav.unlink(missing_ok=True)
    resp.raise_for_status()
    raw = resp.json()

    words, events = [], []
    for w in raw.get("words", []):
        t = w.get("type")
        if t == "word":
            words.append({
                "text": w.get("text", ""),
                "start": float(w.get("start", 0.0)),
                "end": float(w.get("end", 0.0)),
                "speaker": w.get("speaker_id", "speaker_0"),
            })
        elif t == "audio_event":
            events.append({
                "text": w.get("text", ""),
                "start": float(w.get("start", 0.0)),
                "end": float(w.get("end", 0.0)),
            })
    return {"engine": "scribe", "words": words, "events": events}


_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")


def _parse_ts(s: str) -> float:
    m = _TS.search(s)
    if not m:
        return 0.0
    h, mi, se, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + se + ms / 1000.0


def transcribe_captions(url: str, edit_dir: Path, language: str) -> dict:
    """Free fallback: pull caption track via yt-dlp. Line-level timing, spread across words."""
    tmp = edit_dir / "transcripts"
    tmp.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(url).stem or "source")[:60] or "source"
    base = tmp / stem
    subprocess.run(
        ["yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs",
         "--sub-langs", f"{language}.*", "--convert-subs", "vtt", "-o", str(base) + ".%(ext)s", url],
        capture_output=True, text=True,
    )
    vtt = next(iter(sorted(tmp.glob(f"{stem}*.vtt"))), None)
    if not vtt:
        raise RuntimeError("yt-dlp returned no captions for this URL")

    words = []
    cur_start = cur_end = 0.0
    for line in vtt.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "-->" in line:
            a, _, b = line.partition("-->")
            cur_start, cur_end = _parse_ts(a), _parse_ts(b)
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if not text or text.upper() == "WEBVTT":
            continue
        toks = [t for t in text.split() if t]
        if not toks:
            continue
        span = max(cur_end - cur_start, 0.01)
        step = span / len(toks)
        for i, tok in enumerate(toks):
            ws = cur_start + i * step
            words.append({"text": tok, "start": round(ws, 3), "end": round(ws + step, 3), "speaker": "speaker_0"})
    # de-dupe consecutive identical lines that auto-subs repeat
    deduped = []
    for w in words:
        if deduped and deduped[-1]["text"] == w["text"] and abs(deduped[-1]["start"] - w["start"]) < 0.05:
            continue
        deduped.append(w)
    return {"engine": "captions", "words": deduped, "events": []}


def main() -> int:
    _load_env_keys()
    ap = argparse.ArgumentParser(description="Word-level transcript for the video editor.")
    ap.add_argument("source", help="local video path OR a URL")
    ap.add_argument("--edit-dir", default=None, help="output dir (default: <source_parent>/edit)")
    ap.add_argument("--language", default="en")
    ap.add_argument("--num-speakers", type=int, default=None)
    ap.add_argument("--from-json", default=None, help="reuse an existing normalized transcript json")
    ap.add_argument("--prefer", choices=["scribe", "captions"], default="scribe")
    args = ap.parse_args()

    is_url = args.source.startswith(("http://", "https://"))
    parent = Path.cwd() if is_url else Path(args.source).resolve().parent
    edit_dir = Path(args.edit_dir).resolve() if args.edit_dir else parent / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)
    stem = (re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(args.source).stem) or "source")[:60]
    out_path = edit_dir / "transcripts" / f"{stem}.json"

    if out_path.is_file() and not args.from_json:
        print(f"cached {out_path}")
        return 0

    result: dict
    if args.from_json:
        result = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        result.setdefault("engine", "provided")
    else:
        result = None
        errors = []
        order = ["scribe", "captions"] if args.prefer == "scribe" else ["captions", "scribe"]
        for engine in order:
            try:
                if engine == "scribe" and not is_url:
                    result = transcribe_scribe(args.source, edit_dir, args.language, args.num_speakers)
                    break
                if engine == "scribe" and is_url:
                    continue  # scribe needs a local file; download first or use captions
                if engine == "captions" and is_url:
                    result = transcribe_captions(args.source, edit_dir, args.language)
                    break
            except Exception as e:  # noqa: BLE001 — fallback chain
                errors.append(f"{engine}: {e}")
        if result is None:
            print("ERROR: could not transcribe.\n  " + "\n  ".join(errors), file=sys.stderr)
            print("  Fix: set ELEVENLABS_API_KEY for a local file, or pass a URL with captions,"
                  " or supply --from-json.", file=sys.stderr)
            return 2

    result["source"] = args.source
    result["duration"] = probe_duration(args.source) if not is_url else result.get("duration", 0.0)
    if result["duration"] == 0.0 and result.get("words"):
        result["duration"] = round(result["words"][-1]["end"] + 0.5, 3)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"wrote {out_path}  ({len(result.get('words', []))} words, engine={result['engine']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
