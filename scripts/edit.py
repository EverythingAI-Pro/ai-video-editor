#!/usr/bin/env python3
"""
edit.py — the one command that edits a video end-to-end.

This is the automatable path James asked for: "feed it the videos, it does the B-roll,
makes the perfect video + shorts." For the CONVERSATIONAL path, the agent runs the
stages individually (transcribe -> show cut plan -> confirm -> show b-roll plan ->
confirm -> render) so the founder keeps taste control. This script is the same stages
wired straight through for hands-off runs.

Stages: transcribe -> cut plan -> (optional b-roll plan + source) -> render -> shorts.

Usage:
  python edit.py VIDEO [--taste taste.yaml] [--out-dir DIR]
                 [--broll auto|off] [--shorts on|off] [--captions on|off]
                 [--from-transcript T.json]   # skip transcription (reuse)

Everything is taste-driven; flags only override taste for this one run.
Writes all artifacts under <out-dir>/ (default: <video_dir>/edit) and prints a manifest.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ve_taste import load_taste  # noqa: E402


def _run_module(mod: str, *args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(HERE / mod), *args]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{mod} {' '.join(args[:2])} failed:\n{r.stdout}\n{r.stderr}")
    return r


def edit_one(video: str, taste_path: str | None, out_dir: str | None,
             do_broll: str, do_shorts: str, captions: str,
             from_transcript: str | None, publish: str = "on") -> dict:
    video = str(Path(video).resolve())
    taste = load_taste(taste_path)
    base_dir = Path(out_dir).resolve() if out_dir else Path(video).parent / "edit"
    base_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(video).stem
    tdir = base_dir / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    transcript_path = Path(from_transcript) if from_transcript else tdir / f"{stem}.json"

    manifest = {"video": video, "out_dir": str(base_dir), "stages": {}}

    # 1) transcribe
    if not transcript_path.is_file():
        targs = [video, "--edit-dir", str(base_dir)]
        if taste_path:
            pass
        _run_module("ve_transcribe.py", *targs)
    manifest["stages"]["transcript"] = str(transcript_path)

    # 2) cut plan
    edl_path = base_dir / f"{stem}.edl.json"
    cut_args = [str(transcript_path), str(edl_path)]
    if taste_path:
        cut_args += ["--taste", taste_path]
    cp = _run_module("ve_cut.py", "plan", *cut_args)
    manifest["stages"]["cut"] = {"edl": str(edl_path), "summary": cp.stdout.strip()}

    # 3) b-roll (plan + source) — only if on
    broll_plan_path = None
    want_broll = (do_broll == "auto") or (do_broll == "taste" and taste.get("broll", {}).get("enabled", True))
    if want_broll and do_broll != "off":
        plan_path = base_dir / f"{stem}.broll.json"
        bargs = [str(transcript_path), str(edl_path), str(plan_path)]
        if taste_path:
            bargs += ["--taste", taste_path]
        _run_module("ve_broll.py", "plan", *bargs)
        # source the approved objective beats
        assets = base_dir / "broll_assets"
        sourced_path = base_dir / f"{stem}.broll.sourced.json"
        sargs = [str(plan_path), str(assets), str(sourced_path)]
        if taste_path:
            sargs += ["--taste", taste_path]
        try:
            _run_module("ve_broll.py", "source", *sargs)
            broll_plan_path = sourced_path
        except Exception as e:  # sourcing needs network; don't kill the edit
            manifest["stages"]["broll_warning"] = f"b-roll sourcing skipped: {e}"
            broll_plan_path = plan_path
        manifest["stages"]["broll"] = str(broll_plan_path)

    # 4) render the main edit.
    #    On LONG videos we skip captioning the FULL main edit by default: you post the Shorts
    #    (which always get captioned), and burning captions across a 15-min video is slow and
    #    rarely wanted. captions="on" forces it; captions="off" never captions.
    try:
        duration = float(json.loads(transcript_path.read_text()).get("duration", 0) or 0)
    except Exception:
        duration = 0.0
    LONG_S = 240
    caption_main = captions == "on" or (captions == "auto" and duration <= LONG_S)
    out_main = base_dir / f"{stem}.edited.mp4"
    rargs = [video, str(edl_path), str(out_main), "--transcript", str(transcript_path)]
    if broll_plan_path:
        rargs += ["--broll", str(broll_plan_path)]
    if taste_path:
        rargs += ["--taste", taste_path]
    if not caption_main:
        rargs += ["--no-captions"]
    rr = _run_module("ve_render.py", *rargs)
    manifest["stages"]["render"] = {"out": str(out_main), "captioned": caption_main,
                                    "log": rr.stdout.strip()}

    # 5) shorts — virality engine (audio energy + LLM ranker) is the primary picker;
    #    falls back to the transcript-only hook rubric if audio analysis can't run.
    want_shorts = (do_shorts == "on") or (do_shorts == "taste" and taste.get("shorts", {}).get("enabled", True))
    if want_shorts and do_shorts != "off":
        want_n = int(taste.get("shorts", {}).get("count", 3))
        shorts_json = base_dir / f"{stem}.shorts.json"
        clips = []
        ranker = "rubric"
        try:
            vargs = [video, str(transcript_path), str(shorts_json), "--top", str(want_n)]
            if taste_path:
                vargs += ["--taste", taste_path]
            _run_module("ve_virality.py", *vargs)
            vmeta = json.loads(shorts_json.read_text())
            clips = vmeta.get("clips", [])
            ranker = vmeta.get("ranker", "audio")
        except Exception as e:
            manifest.setdefault("short_warnings", []).append(f"virality failed, using rubric: {e}")
        if not clips:  # honest fallback
            shargs = [str(transcript_path), str(shorts_json)]
            if taste_path:
                shargs += ["--taste", taste_path]
            sh = _run_module("ve_shorts.py", "find", *shargs)
            clips = json.loads(shorts_json.read_text()).get("shorts", [])
            ranker = "rubric"

        rendered = []
        for i, s in enumerate(clips):
            edl = s.get("edl") or {"ranges": [{"source_start": s["source_start"],
                                               "source_end": s["source_end"], "out_offset": 0.0}]}
            s_edl = base_dir / f"{stem}.short{i}.edl.json"
            s_edl.write_text(json.dumps(edl, indent=2))
            s_out = base_dir / f"{stem}.short{i}.mp4"
            sr = [video, str(s_edl), str(s_out), "--transcript", str(transcript_path)]
            if taste_path:
                sr += ["--taste", taste_path]
            try:
                _run_module("ve_render.py", *sr)
                rendered.append(str(s_out))
            except Exception as e:
                manifest.setdefault("short_warnings", []).append(str(e))
        manifest["stages"]["shorts"] = {"meta": str(shorts_json), "rendered": rendered,
                                        "ranker": ranker, "count": len(rendered)}

    # 6) publish layer — post pack (captions/hooks/hashtags) + a beautiful HTML review page
    if publish != "off":
        shorts_json = base_dir / f"{stem}.shorts.json"
        if not shorts_json.is_file():
            shorts_json.write_text(json.dumps({"shorts": []}))
        pack_json = base_dir / f"{stem}.postpack.json"
        pargs = [str(transcript_path), str(shorts_json), str(pack_json)]
        if taste_path:
            pargs += ["--taste", taste_path]
        _run_module("ve_postpack.py", *pargs)

        rendered_shorts = manifest.get("stages", {}).get("shorts", {}).get("rendered", [])
        html_out = base_dir / "review.html"
        habs = [str(html_out), "--main", str(out_main), "--postpack", str(pack_json),
                "--title", Path(video).stem.replace("_", " ").title()]
        for rs in rendered_shorts:
            habs += ["--short", rs]
        if taste_path:
            habs += ["--taste", taste_path]
        _run_module("ve_publish.py", *habs)
        manifest["stages"]["publish"] = {"postpack": str(pack_json), "review_page": str(html_out)}

    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Edit a video end-to-end.")
    ap.add_argument("video")
    ap.add_argument("--taste", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--broll", choices=["auto", "off", "taste"], default="taste")
    ap.add_argument("--shorts", choices=["on", "off", "taste"], default="taste")
    ap.add_argument("--captions", choices=["on", "off", "auto"], default="auto",
                    help="auto = caption short videos + all Shorts, skip the full long main edit")
    ap.add_argument("--publish", choices=["on", "off"], default="on",
                    help="also build the post pack + HTML review page (default on)")
    ap.add_argument("--from-transcript", default=None)
    args = ap.parse_args()

    manifest = edit_one(args.video, args.taste, args.out_dir, args.broll,
                        args.shorts, args.captions, args.from_transcript, args.publish)
    print("\n=== EDIT COMPLETE ===")
    print(f"Main edit: {manifest['stages'].get('render', {}).get('out')}")
    if "shorts" in manifest["stages"]:
        for r in manifest["stages"]["shorts"]["rendered"]:
            print(f"Short:     {r}")
    if "publish" in manifest["stages"]:
        print(f"Review page: {manifest['stages']['publish']['review_page']}  ← open this, copy captions, post")
    mpath = Path(manifest["out_dir"]) / "manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2))
    print(f"Manifest:  {mpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
