#!/usr/bin/env python3
"""Watch 01_RAW on AI-1, transcribe locally with Whisper, then run AI Video Editor.

Outputs:
  ~/content-system/02_READY/<video-name>/
  ~/content-system/03_POSTIZ_UPLOAD/<video-name>/
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("AI_EDITOR_REPO", HERE)).resolve()
EDIT_PY = REPO / "scripts" / "edit.py"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
CONTENT_ROOT = Path(os.environ.get("CONTENT_ROOT", Path.home() / "content-system")).resolve()
RAW = CONTENT_ROOT / "01_RAW"
READY = CONTENT_ROOT / "02_READY"
UPLOAD = CONTENT_ROOT / "03_POSTIZ_UPLOAD"
LOGS = CONTENT_ROOT / "logs"

_model = None


def log(message: str) -> None:
    print(f"[content-pipeline] {message}", flush=True)


def load_model():
    global _model
    if _model is not None:
        return _model
    from faster_whisper import WhisperModel

    preferred_device = os.environ.get("WHISPER_DEVICE", "cuda")
    preferred_compute = os.environ.get("WHISPER_COMPUTE", "float16")
    model_size = os.environ.get("WHISPER_MODEL", "small")
    try:
        _model = WhisperModel(model_size, device=preferred_device, compute_type=preferred_compute)
        log(f"Whisper model loaded: {model_size} on {preferred_device}/{preferred_compute}")
    except Exception as exc:
        log(f"CUDA load failed ({exc}); falling back to CPU/int8")
        _model = WhisperModel(model_size, device="cpu", compute_type="int8")
        log(f"Whisper model loaded: {model_size} on cpu/int8")
    return _model


def probe_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def transcribe(video: Path, out_dir: Path) -> Path:
    transcript_dir = out_dir / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / f"{video.stem}.json"

    model = load_model()
    segments, info = model.transcribe(
        str(video),
        language=os.environ.get("WHISPER_LANGUAGE") or None,
        task="transcribe",
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    words = []
    events = []
    for segment in segments:
        for word in segment.words or []:
            text = (word.word or "").strip()
            if not text:
                continue
            words.append(
                {
                    "text": text,
                    "start": round(float(word.start), 3),
                    "end": round(float(word.end), 3),
                    "speaker": "speaker_0",
                }
            )
        if getattr(segment, "no_speech_prob", 0.0) >= 0.95:
            events.append(
                {
                    "text": "(no speech)",
                    "start": round(float(segment.start), 3),
                    "end": round(float(segment.end), 3),
                }
            )

    duration = probe_duration(video) or (words[-1]["end"] + 0.5 if words else 0.0)
    payload = {
        "source": str(video),
        "engine": f"faster-whisper-{os.environ.get('WHISPER_MODEL', 'small')}",
        "language": getattr(info, "language", None),
        "words": words,
        "events": events,
        "duration": round(duration, 3),
    }
    transcript_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log(f"transcribed {len(words)} words -> {transcript_path}")
    return transcript_path


def stage_outputs(out_dir: Path) -> None:
    stage_dir = UPLOAD / out_dir.name
    stage_dir.mkdir(parents=True, exist_ok=True)
    for file in out_dir.rglob("*"):
        if file.is_file() and file.suffix.lower() in {".mp4", ".mov"}:
            destination = stage_dir / file.name
            if not destination.exists() or destination.stat().st_mtime != file.stat().st_mtime:
                shutil.copy2(file, destination)
                log(f"staged {destination}")


def process(video: Path, taste: Path | None, broll: str) -> bool:
    out_dir = READY / video.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    done = out_dir / f"{video.stem}.done"
    if done.exists():
        return False

    log(f"processing {video.name}")
    try:
        transcript = transcribe(video, out_dir)
        cmd = [
            sys.executable,
            str(EDIT_PY),
            str(video),
            "--out-dir",
            str(out_dir),
            "--from-transcript",
            str(transcript),
            "--broll",
            broll,
            "--shorts",
            "on",
            "--captions",
            "auto",
            "--publish",
            "on",
        ]
        if taste:
            cmd.extend(["--taste", str(taste)])
        result = subprocess.run(cmd, capture_output=True, text=True)
        (out_dir / "pipeline.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-3000:] or result.stdout[-3000:])
        stage_outputs(out_dir)
        done.write_text("ok\n", encoding="utf-8")
        log(f"done {video.name} -> {out_dir}")
        return True
    except Exception as exc:
        (out_dir / f"{video.stem}.failed").write_text(str(exc), encoding="utf-8")
        log(f"FAILED {video.name}: {exc}")
        return False


def scan(taste: Path | None, broll: str) -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    READY.mkdir(parents=True, exist_ok=True)
    UPLOAD.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    processed = 0
    for video in sorted(RAW.iterdir()):
        if video.is_file() and video.suffix.lower() in VIDEO_EXT and not video.name.startswith("."):
            if (RAW / f"{video.stem}.studio").exists():
                continue  # routed to the studio pipeline instead
            if process(video, taste, broll):
                processed += 1
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Whisper + AI Video Editor pipeline watcher.")
    parser.add_argument("--once", action="store_true", help="Process current files and exit.")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--taste", type=Path, default=REPO / "taste.yaml")
    parser.add_argument("--broll", choices=["auto", "off", "taste"], default=os.environ.get("BROLL_MODE", "off"))
    args = parser.parse_args()

    if args.once:
        count = scan(args.taste, args.broll)
        log(f"processed {count} new video(s)")
        return 0

    log(f"watching {RAW} every {args.interval}s")
    try:
        while True:
            scan(args.taste, args.broll)
            time.sleep(max(5, args.interval))
    except KeyboardInterrupt:
        log("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
