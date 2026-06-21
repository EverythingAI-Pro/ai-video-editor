#!/usr/bin/env python3
"""
ve_post.py — OPTIONAL auto-posting. Off by default. Needs the founder's own keys.

Most founders should just use the HTML review page (ve_publish.py) and the copy buttons —
native uploads to TikTok/Reels/Shorts get better reach than API posts anyway, and there are
no keys to manage. This script exists for those who want true hands-off scheduling.

Supported now:
  - Buffer  (one token, schedules to all their connected channels). Set BUFFER_ACCESS_TOKEN.
  - A generic webhook (Make/Zapier/n8n) — POST the post pack JSON to your own automation.

It NEVER posts without an explicit flag, and it prints exactly what it will do first.
Video upload to each native platform's API is intentionally NOT built here: those APIs require
per-platform app review + OAuth that a founder shouldn't wrangle. Buffer / a webhook is the
sane bridge. If you connect a platform's API yourself, add it as a provider below.

CLI:
  ve_post.py plan    <postpack.json>                       # show what would be posted, post nothing
  ve_post.py buffer  <postpack.json> --confirm             # schedule text posts via Buffer
  ve_post.py webhook <postpack.json> --url <URL> --confirm # POST the pack to your automation
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_env():
    here = Path(__file__).resolve().parent.parent
    for c in [here / ".env"]:
        if c.is_file():
            for line in c.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def plan(pack: dict) -> None:
    print("Would post (nothing sent — this is a dry run):")
    for it in pack.get("items", []):
        tk = it.get("platforms", {}).get("tiktok", {})
        cap = tk.get("caption", "")
        tags = " ".join(tk.get("hashtags", []))
        print(f"  • {it['label']}: {cap[:70]}…  {tags}")
    print("\nTo actually schedule: ve_post.py buffer <pack> --confirm  (needs BUFFER_ACCESS_TOKEN)")
    print("Or native-upload each clip from the HTML review page (recommended for reach).")


def post_buffer(pack: dict, confirm: bool) -> int:
    import requests

    token = os.environ.get("BUFFER_ACCESS_TOKEN")
    if not token:
        print("ERROR: set BUFFER_ACCESS_TOKEN (Buffer → Developers → Access Token) to use this.")
        return 2
    # list profiles
    prof = requests.get("https://api.bufferapp.com/1/profiles.json",
                        params={"access_token": token}, timeout=30)
    prof.raise_for_status()
    profiles = [p["id"] for p in prof.json()]
    if not profiles:
        print("No Buffer channels connected. Connect them in Buffer first.")
        return 2
    if not confirm:
        print(f"Would schedule {len(pack.get('items', []))} posts to {len(profiles)} Buffer channel(s).")
        print("Re-run with --confirm to actually schedule.")
        return 0
    sent = 0
    for it in pack.get("items", []):
        tk = it.get("platforms", {}).get("tiktok", {})
        text = (tk.get("caption", "") + "\n\n" + " ".join(tk.get("hashtags", []))).strip()
        r = requests.post("https://api.bufferapp.com/1/updates/create.json",
                          data={"access_token": token, "profile_ids[]": profiles, "text": text},
                          timeout=30)
        if r.ok:
            sent += 1
        else:
            print(f"  ! Buffer rejected '{it['label']}': {r.text[:120]}")
    print(f"scheduled {sent} post(s) to Buffer. (Attach the video files in Buffer before publishing.)")
    return 0


def post_webhook(pack: dict, url: str, confirm: bool) -> int:
    import requests

    if not confirm:
        print(f"Would POST the post pack to {url}. Re-run with --confirm.")
        return 0
    r = requests.post(url, json=pack, timeout=30)
    print(f"webhook responded {r.status_code}")
    return 0 if r.ok else 1


def main() -> int:
    _load_env()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("pack")
    b = sub.add_parser("buffer")
    b.add_argument("pack")
    b.add_argument("--confirm", action="store_true")
    w = sub.add_parser("webhook")
    w.add_argument("pack")
    w.add_argument("--url", required=True)
    w.add_argument("--confirm", action="store_true")
    args = ap.parse_args()
    pack = json.loads(Path(args.pack).read_text())

    if args.cmd == "plan":
        plan(pack)
        return 0
    if args.cmd == "buffer":
        return post_buffer(pack, args.confirm)
    if args.cmd == "webhook":
        return post_webhook(pack, args.url, args.confirm)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
