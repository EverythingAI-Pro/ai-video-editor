#!/usr/bin/env python3
"""
autopilot.py — drop videos in a folder, get edited videos + shorts back. Hands-off.

This is the "feed it the videos and it does everything" path. Point it at an inbox
folder; every new video gets the full edit.py treatment using the founder's taste.yaml.
Outputs land in <inbox>/edited/ and a .done marker prevents re-processing.

Two modes:
  once   — process everything currently in the inbox, then exit (good for a scheduled run)
  watch  — keep running, process new files as they land (poll every --interval seconds)

Usage:
  python autopilot.py once  <inbox_dir> [--taste taste.yaml]
  python autopilot.py watch <inbox_dir> [--taste taste.yaml] [--interval 20]

A founder never needs the CLI: FounderOS calls this for them. But it's a plain script,
so it also drops into cron / a launch agent for true set-and-forget.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}


def _process(video: Path, out_dir: Path, taste: str | None) -> bool:
    done = out_dir / f"{video.stem}.done"
    if done.exists():
        return False
    print(f"[autopilot] editing {video.name} …", flush=True)
    cmd = [sys.executable, str(HERE / "edit.py"), str(video), "--out-dir", str(out_dir)]
    if taste:
        cmd += ["--taste", taste]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        done.write_text("ok\n")
        print(f"[autopilot] done {video.name} -> {out_dir}", flush=True)
        return True
    # write a failure marker so we don't loop forever on a bad file
    (out_dir / f"{video.stem}.failed").write_text(r.stderr[-2000:])
    print(f"[autopilot] FAILED {video.name} (see {video.stem}.failed)", flush=True)
    return False


def scan(inbox: Path, taste: str | None) -> int:
    out_dir = inbox / "edited"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for v in sorted(inbox.iterdir()):
        if v.is_file() and v.suffix.lower() in VIDEO_EXT:
            if _process(v, out_dir, taste):
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("once")
    o.add_argument("inbox")
    o.add_argument("--taste", default=None)
    w = sub.add_parser("watch")
    w.add_argument("inbox")
    w.add_argument("--taste", default=None)
    w.add_argument("--interval", type=int, default=20)
    args = ap.parse_args()

    inbox = Path(args.inbox).resolve()
    inbox.mkdir(parents=True, exist_ok=True)

    if args.cmd == "once":
        n = scan(inbox, args.taste)
        print(f"[autopilot] processed {n} new video(s)")
        return 0
    if args.cmd == "watch":
        print(f"[autopilot] watching {inbox} (every {args.interval}s). Drop videos in; Ctrl-C to stop.")
        try:
            while True:
                scan(inbox, args.taste)
                time.sleep(max(5, args.interval))
        except KeyboardInterrupt:
            print("\n[autopilot] stopped")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
