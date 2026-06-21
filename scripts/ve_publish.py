#!/usr/bin/env python3
"""
ve_publish.py — build a beautiful, self-contained HTML review page for an edit.

This is the "put it into HTML + help them post" deliverable. Open it in a browser and you get:
  - the finished main edit, playable
  - every Short, playable, side by side
  - for each, a platform switcher (TikTok / Reels / Shorts / X / LinkedIn) with a ready
    caption + hook + hashtags and a one-click COPY button
  - a posting checklist

No server, no build step, no internet — one HTML file you can double-click. Videos are referenced
by relative path (kept next to the HTML), so the whole folder is portable / shareable.

CLI:
  ve_publish.py <out.html> --main <edited.mp4> [--short s0.mp4 --short s1.mp4 ...]
                --postpack <postpack.json> [--title "My Video"] [--taste taste.yaml]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ve_taste import load_taste  # noqa: E402


def _rel(target: str, start_dir: Path) -> str:
    try:
        return os.path.relpath(Path(target).resolve(), start_dir).replace(os.sep, "/")
    except Exception:
        return target


def _video_card(idx: int, label: str, src: str, post: dict | None) -> str:
    platforms = (post or {}).get("platforms", {})
    tabs, panels = [], []
    order = ["tiktok", "reels", "shorts", "x", "linkedin"]
    first = True
    for key in order:
        p = platforms.get(key)
        if not p:
            continue
        active = " active" if first else ""
        tabs.append(
            f'<button class="tab{active}" data-card="{idx}" data-plat="{key}" '
            f'onclick="showPlat({idx},\'{key}\')">{html.escape(p["platform"])}</button>'
        )
        tags = " ".join(html.escape(t) for t in p.get("hashtags", []))
        caption_full = f'{p.get("caption", "")}\n\n{tags}'.strip()
        panels.append(
            f'<div class="panel{active}" id="panel-{idx}-{key}">'
            f'<div class="lbl">Hook</div><div class="hook">{html.escape(p.get("hook", ""))}</div>'
            f'<div class="lbl">Caption</div><div class="cap">{html.escape(p.get("caption", ""))}</div>'
            f'<div class="lbl">Hashtags</div><div class="tags">{tags}</div>'
            f'<button class="copy" onclick="copyText(this, {json.dumps(caption_full)})">'
            f'Copy caption + hashtags</button>'
            f"</div>"
        )
    post_html = ""
    if tabs:
        post_html = (
            f'<div class="tabs">{"".join(tabs)}</div><div class="panels">{"".join(panels)}</div>'
        )
    return f"""
    <div class="card">
      <div class="vidwrap"><video src="{html.escape(src)}" controls preload="metadata"></video></div>
      <div class="meta">
        <h3>{html.escape(label)}</h3>
        {post_html}
      </div>
    </div>"""


def build_html(out_html: str, main: str, shorts: list[str], postpack: dict,
               title: str, taste: dict) -> str:
    out_dir = Path(out_html).resolve().parent
    brand = taste.get("brand", {})
    primary = brand.get("primary_color", "#FF5A00")
    accent = brand.get("accent_color", "#22C55E")
    by_id = {it["id"]: it for it in postpack.get("items", [])}

    cards = []
    if main:
        cards.append(_video_card(0, "Final edit", _rel(main, out_dir), by_id.get("main")))
    for i, s in enumerate(shorts):
        post = by_id.get(f"short{i}")
        label = post["label"] if post else f"Short #{i}"
        cards.append(_video_card(i + 1, label, _rel(s, out_dir), post))

    checklist = """
      <li>Post the Final edit to YouTube / LinkedIn (longer-form).</li>
      <li>Post each Short to TikTok, Reels, and YouTube Shorts (native upload, not a link).</li>
      <li>Paste the platform caption + hashtags (copy buttons above).</li>
      <li>Pin a comment with your CTA (link in bio / "comment WORD").</li>
      <li>Post Shorts 3–5 hours apart, not all at once.</li>"""

    note = html.escape(postpack.get("note", ""))
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(title)} — Video review &amp; post pack</title>
<style>
  :root {{ --p:{primary}; --a:{accent}; --bg:#0b0d12; --card:#141821; --txt:#e8edf5; --dim:#8b94a7; --line:#222838; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--txt); font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  header {{ padding:28px 24px; border-bottom:1px solid var(--line); background:linear-gradient(180deg,#0f1219,transparent); }}
  header h1 {{ margin:0 0 6px; font-size:24px; }}
  header .sub {{ color:var(--dim); font-size:13px; }}
  header .pill {{ display:inline-block; margin-top:10px; padding:4px 10px; border-radius:999px; background:color-mix(in srgb,var(--p) 18%,transparent); color:var(--p); font-size:12px; font-weight:600; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:24px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:20px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; overflow:hidden; display:flex; flex-direction:column; }}
  .vidwrap {{ background:#000; aspect-ratio:9/16; display:flex; align-items:center; justify-content:center; }}
  .vidwrap video {{ width:100%; height:100%; object-fit:contain; background:#000; }}
  .meta {{ padding:14px 14px 16px; }}
  .meta h3 {{ margin:0 0 10px; font-size:15px; }}
  .tabs {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:10px; }}
  .tab {{ background:#0e131b; color:var(--dim); border:1px solid var(--line); border-radius:8px; padding:5px 9px; font-size:12px; cursor:pointer; }}
  .tab.active {{ color:var(--txt); border-color:var(--p); background:color-mix(in srgb,var(--p) 14%,transparent); }}
  .panel {{ display:none; }}
  .panel.active {{ display:block; }}
  .lbl {{ color:var(--dim); font-size:11px; text-transform:uppercase; letter-spacing:.04em; margin:8px 0 3px; }}
  .hook {{ font-weight:600; }}
  .cap {{ white-space:pre-wrap; }}
  .tags {{ color:var(--a); font-size:13px; word-break:break-word; }}
  .copy {{ margin-top:12px; width:100%; padding:9px; border:none; border-radius:9px; background:var(--p); color:#fff; font-weight:600; cursor:pointer; }}
  .copy.ok {{ background:var(--a); }}
  .checklist {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:18px 22px; margin-top:26px; }}
  .checklist h2 {{ margin:0 0 8px; font-size:16px; }}
  .checklist ol {{ margin:0; padding-left:18px; color:var(--txt); }}
  .checklist li {{ margin:5px 0; }}
  footer {{ color:var(--dim); font-size:12px; text-align:center; padding:30px; }}
  .note {{ color:var(--dim); font-size:12px; margin-top:6px; }}
</style></head>
<body>
<header>
  <h1>{html.escape(title)}</h1>
  <div class="sub">Edited video + ready-to-post Shorts. Pick a platform, copy the caption, post it.</div>
  <span class="pill">AI Video Editor</span>
</header>
<div class="wrap">
  <div class="grid">{"".join(cards)}</div>
  <div class="checklist">
    <h2>Posting checklist</h2>
    <ol>{checklist}</ol>
    <div class="note">{note}</div>
  </div>
</div>
<footer>Generated by the AI Video Editor · edit by conversation, post in minutes.</footer>
<script>
  function showPlat(card, plat) {{
    document.querySelectorAll('.tab[data-card="'+card+'"]').forEach(function(t){{
      t.classList.toggle('active', t.dataset.plat===plat);
    }});
    document.querySelectorAll('[id^="panel-'+card+'-"]').forEach(function(p){{
      p.classList.toggle('active', p.id==='panel-'+card+'-'+plat);
    }});
  }}
  function copyText(btn, text) {{
    navigator.clipboard.writeText(text).then(function(){{
      var old = btn.textContent; btn.textContent='Copied ✓'; btn.classList.add('ok');
      setTimeout(function(){{ btn.textContent=old; btn.classList.remove('ok'); }}, 1400);
    }});
  }}
</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--main", default=None)
    ap.add_argument("--short", action="append", default=[], dest="shorts")
    ap.add_argument("--postpack", required=True)
    ap.add_argument("--title", default="My Video")
    ap.add_argument("--taste", default=None)
    args = ap.parse_args()
    taste = load_taste(args.taste)
    postpack = json.loads(Path(args.postpack).read_text())
    page = build_html(args.out, args.main, args.shorts, postpack, args.title, taste)
    Path(args.out).write_text(page, encoding="utf-8")
    print(f"review page -> {args.out}  ({len(args.shorts)} shorts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
