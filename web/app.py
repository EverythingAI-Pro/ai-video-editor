#!/usr/bin/env python3
"""CyrusMiner AI Video Editor — web dashboard.

Web front-end for the AI1 content pipeline:
  01_RAW -> (content-editor autopilot) -> 02_READY -> 03_POSTIZ_UPLOAD -> 04_PUBLISHED

Features:
  - Password login (signed cookie)
  - Upload raw videos (autopilot picks them up within ~30s)
  - Optional Creative Direction brief + platform pick per upload (routes to studio)
  - Live pipeline status per job
  - Play outputs, open review.html, read post-pack copy, download files
  - Retry failed jobs / force re-edit
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import shutil
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse)

CONTENT_ROOT = Path(os.environ.get("CONTENT_ROOT", "/home/cyrusminer/content-system"))
RAW = CONTENT_ROOT / "01_RAW"
READY = CONTENT_ROOT / "02_READY"
UPLOAD = CONTENT_ROOT / "03_POSTIZ_UPLOAD"
PUBLISHED = CONTENT_ROOT / "04_PUBLISHED"

PASSWORD = os.environ.get("VIDEO_WEB_PASSWORD", "change-me")
SECRET = os.environ.get("VIDEO_WEB_SECRET") or hashlib.sha256(PASSWORD.encode()).hexdigest()
COOKIE = "ve_session"
SESSION_TOKENS: set[str] = set()

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}

app = FastAPI(title="AI Video Editor", docs_url=None, redoc_url=None)

# ---------------------------------------------------------------- auth

def _token_ok(token: str | None) -> bool:
    return bool(token) and token in SESSION_TOKENS


def _authed(request: Request) -> bool:
    return _token_ok(request.cookies.get(COOKIE))


@app.middleware("http")
async def gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/login") or path == "/healthz":
        return await call_next(request)
    if not _authed(request):
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


LOGIN_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>AI Video Editor — Sign in</title>
<style>
 body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
 background:#0b0d10;color:#e8eaed;font-family:-apple-system,'Segoe UI',Roboto,sans-serif}
 .card{background:#14171c;border:1px solid #23282f;border-radius:16px;padding:40px;width:340px;
 box-shadow:0 20px 60px #0008}
 h1{font-size:20px;margin:0 0 6px} p.sub{color:#8b94a1;font-size:13px;margin:0 0 24px}
 input{width:100%;box-sizing:border-box;padding:12px 14px;border-radius:10px;border:1px solid #2b313a;
 background:#0e1114;color:#e8eaed;font-size:14px;outline:none}
 input:focus{border-color:#4f8cff}
 button{margin-top:14px;width:100%;padding:12px;border:0;border-radius:10px;background:#4f8cff;
 color:#fff;font-weight:600;font-size:14px;cursor:pointer}
 button:hover{background:#3f7bef} .err{color:#ff6b6b;font-size:13px;margin-top:12px;min-height:16px}
 .logo{font-size:34px;margin-bottom:10px}
</style></head><body>
<form class=card method=post action=/login>
 <div class=logo>🎬</div>
 <h1>AI Video Editor</h1>
 <p class=sub>CyrusMiner content pipeline — AI1</p>
 <input type=password name=password placeholder="Password" autofocus>
 <button>Sign in</button>
 <div class=err>__ERR__</div>
</form></body></html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return LOGIN_PAGE.replace("__ERR__", "")


@app.post("/login")
async def login_do(password: str = Form(...)):
    if hmac.compare_digest(password, PASSWORD):
        token = secrets.token_urlsafe(32)
        SESSION_TOKENS.add(token)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=30 * 86400)
        return resp
    return HTMLResponse(LOGIN_PAGE.replace("__ERR__", "Wrong password."), status_code=401)


@app.get("/logout")
async def logout(request: Request):
    SESSION_TOKENS.discard(request.cookies.get(COOKIE, ""))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/healthz")
async def healthz():
    return PlainTextResponse("ok")

# ---------------------------------------------------------------- model

def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _ago(ts: float) -> str:
    if not ts:
        return "—"
    d = time.time() - ts
    if d < 60:
        return f"{int(d)}s ago"
    if d < 3600:
        return f"{int(d/60)}m ago"
    if d < 86400:
        return f"{d/3600:.1f}h ago"
    return f"{int(d/86400)}d ago"


def jobs() -> list[dict]:
    """One entry per video, merged across the four pipeline folders."""
    out: dict[str, dict] = {}

    def entry(name: str) -> dict:
        return out.setdefault(name, {
            "name": name, "status": "unknown", "raw": None, "ready": None,
            "staged": None, "published": None, "updated": 0.0, "failed": "",
        })

    for f in sorted(RAW.glob("*")) if RAW.exists() else []:
        if f.is_file() and f.suffix.lower() in VIDEO_EXT and not f.name.startswith("."):
            e = entry(f.stem)
            e["raw"] = f
            e["updated"] = max(e["updated"], _mtime(f))

    for d in sorted(READY.glob("*")) if READY.exists() else []:
        if d.is_dir():
            e = entry(d.name)
            e["ready"] = d
            done = d / f"{d.name}.done"
            failed = d / f"{d.name}.failed"
            sdone = d / "studio" / "studio.done"
            sfail = d / "studio" / "studio.failed"
            if failed.exists() or sfail.exists():
                e["status"] = "failed"
                src = failed if failed.exists() else sfail
                e["failed"] = src.read_text(encoding="utf-8", errors="replace")[-400:]
                e["updated"] = max(e["updated"], _mtime(src))
            elif done.exists() or sdone.exists():
                e["status"] = "ready"
                e["updated"] = max(e["updated"], _mtime(done if done.exists() else sdone))
            else:
                e["status"] = "processing"
                e["updated"] = max(e["updated"], _mtime(d))

    for d in sorted(UPLOAD.glob("*")) if UPLOAD.exists() else []:
        if d.is_dir():
            e = entry(d.name)
            e["staged"] = d
            e["updated"] = max(e["updated"], _mtime(d))

    for d in sorted(PUBLISHED.glob("*")) if PUBLISHED.exists() else []:
        if d.is_dir():
            e = entry(d.name)
            e["published"] = d
            e["updated"] = max(e["updated"], _mtime(d))

    for e in out.values():
        if e["published"]:
            e["status"] = "published"
        elif e["status"] == "unknown":
            e["status"] = "queued" if e["raw"] else ("ready" if e["ready"] else "queued")
        elif e["status"] == "ready" and e["raw"] is None and e["ready"] is None:
            e["status"] = "staged"

    return sorted(out.values(), key=lambda e: -e["updated"])


# ---------------------------------------------------------------- pages

CSS = """
:root{--bg:#0b0d10;--card:#14171c;--line:#23282f;--txt:#e8eaed;--mut:#8b94a1;--acc:#4f8cff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font-family:-apple-system,'Segoe UI',Roboto,sans-serif}
header{display:flex;align-items:center;gap:14px;padding:16px 28px;border-bottom:1px solid var(--line);
 position:sticky;top:0;background:#0b0d10ee;backdrop-filter:blur(8px);z-index:9}
header h1{font-size:17px;margin:0}
header .sp{flex:1}
a{color:var(--acc);text-decoration:none}
.btn{display:inline-block;background:#1c2129;border:1px solid var(--line);color:var(--txt);
 padding:8px 14px;border-radius:9px;font-size:13px;cursor:pointer}
.btn:hover{border-color:var(--acc)}
.btn.pri{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
main{max-width:1180px;margin:0 auto;padding:26px 28px 80px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px;margin-top:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
.card h3{margin:0 0 4px;font-size:15px;word-break:break-all}
.meta{color:var(--mut);font-size:12px;margin-bottom:12px}
.badge{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;
 padding:3px 9px;border-radius:20px;margin-bottom:10px}
.b-queued{background:#2b2f17;color:#d7e06b}.b-processing{background:#12305c;color:#7db4ff}
.b-ready{background:#123c2a;color:#5ee0a0}.b-failed{background:#4a1620;color:#ff8b8b}
.b-staged{background:#3a2a10;color:#f0b95e}.b-published{background:#2d1a4a;color:#c69bff}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.upzone{border:2px dashed #2b313a;border-radius:14px;padding:30px;text-align:center;color:var(--mut);
 margin-top:20px;transition:.15s}
.upzone:hover{border-color:var(--acc);color:var(--txt)}
.upzone input{display:none}
.stat{display:inline-block;margin-right:22px;color:var(--mut);font-size:13px}
.stat b{color:var(--txt);font-size:18px;margin-right:5px}
video{max-width:100%;border-radius:10px;background:#000}
.fileitem{display:flex;align-items:center;gap:10px;padding:9px 12px;background:#0e1114;
 border:1px solid var(--line);border-radius:10px;margin-top:8px;font-size:13px}
.fileitem .sz{color:var(--mut);margin-left:auto;white-space:nowrap}
pre{background:#0e1114;border:1px solid var(--line);border-radius:10px;padding:14px;font-size:12px;
 overflow:auto;max-height:420px;color:#b9c2cf}
iframe{width:100%;height:70vh;border:1px solid var(--line);border-radius:12px;background:#fff}
.tabs{display:flex;gap:6px;margin:18px 0 14px;flex-wrap:wrap}
.tabs a{padding:7px 14px;border-radius:9px;background:#1c2129;border:1px solid var(--line);
 color:var(--mut);font-size:13px}
.tabs a.on{color:#fff;border-color:var(--acc);background:#16233c}
h2.sec{font-size:14px;color:var(--mut);text-transform:uppercase;letter-spacing:.6px;margin:26px 0 4px}
"""


def _head(title: str) -> str:
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{_esc(title)} · AI Video Editor</title><style>{CSS}</style>
<meta http-equiv=refresh content="45"></head><body>
<header><span style="font-size:20px">🎬</span><h1>{_esc(title)}</h1><span class=sp></span>
<a class=btn href="/">Dashboard</a>
<a class=btn href="https://postiz.nimaataie.com" target=_blank>Postiz ↗</a>
<a class=btn href="/logout">Sign out</a></header><main>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    js = jobs()
    counts = {}
    for j in js:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    parts = [_head("Dashboard")]
    parts.append(
        f"<div><span class=stat><b>{len(js)}</b>jobs</span>"
        + "".join(f"<span class=stat><b>{n}</b>{_esc(s)}</span>" for s, n in sorted(counts.items()))
        + "</div>"
    )
    parts.append("""
<label class=upzone id=uz>
 <input type=file id=uf multiple accept="video/*">
 <div style="font-size:26px">⬆️</div>
 <div style="margin:8px 0 2px;font-weight:600">Drop raw videos here (or click to choose)</div>
 <div style="font-size:12px">Uploading starts a full studio production job automatically</div>
 <textarea id=ubrief placeholder="Creative direction (optional). Paste a Kimi strategy here, or leave empty for fully automatic." style="margin-top:14px;width:100%;box-sizing:border-box;min-height:64px;background:#0e1114;border:1px solid #2b313a;border-radius:10px;color:#e8eaed;padding:10px;font-size:13px;font-family:inherit"></textarea>
 <div style="margin-top:10px;font-size:12px;color:var(--mut)">Platforms (optional): <label><input type=checkbox class=upf value=youtube checked> YouTube</label> <label><input type=checkbox class=upf value=x checked> X</label> <label><input type=checkbox class=upf value=telegram checked> Telegram</label> <label><input type=checkbox class=upf value=mastodon checked> Mastodon</label> <label><input type=checkbox class=upf value=hashnode> Hashnode</label> <label><input type=checkbox class=upf value=nostr> Nostr</label></div>
 <div id=uprog style="margin-top:10px;font-size:13px;color:#5ee0a0"></div>
</label>
<script>
const uz=document.getElementById('uz'), uf=document.getElementById('uf'), pr=document.getElementById('uprog');
async function send(files){
 for(const f of files){
  pr.textContent='Uploading '+f.name+' …';
  const fd=new FormData(); fd.append('file',f);
  const b=document.getElementById('ubrief').value.trim();
  const pl=[...document.querySelectorAll('.upf:checked')].map(x=>x.value).join(',');
  if(b) fd.append('brief',b);
  if(pl) fd.append('platforms',pl);
  const r=await fetch('/upload',{method:'POST',body:fd});
  pr.textContent = r.ok ? ('Production job started: '+f.name+' ✓') : ('FAILED: '+f.name);
 }
 setTimeout(()=>location.reload(), 800);
}
uf.onchange=()=>send(uf.files);
uz.ondragover=e=>{e.preventDefault()};
uz.ondrop=e=>{e.preventDefault();send(e.dataTransfer.files)};
</script>""")

    if not js:
        parts.append("<div class=card style=margin-top:20px>No jobs yet — upload your first video above.</div>")
    else:
        parts.append("<div class=grid>")
        for j in js:
            name = _esc(j["name"])
            st = j["status"]
            thumb = ""
            if j["ready"]:
                shorts = sorted(j["ready"].glob("*.short*.mp4")) or sorted(j["ready"].rglob("*.mp4"))
                if shorts:
                    rel = shorts[0].relative_to(CONTENT_ROOT)
                    thumb = f'<video src="/files/{_esc(str(rel))}" preload=metadata muted playsinline style="margin-top:10px"></video>'
            flags = []
            if j["raw"]:
                flags.append(f"raw {_human(j['raw'].stat().st_size)}")
            if j["staged"]:
                flags.append(f"{sum(1 for _ in j['staged'].glob('*.mp4'))} staged")
            parts.append(
                f'<div class=card><span class="badge b-{st}">{st}</span>'
                f"<h3>{name}</h3><div class=meta>updated {_ago(j['updated'])}"
                + (f" · {' · '.join(_esc(f) for f in flags)}" if flags else "")
                + f"</div>{thumb}"
                f'<div class=row><a class=btn pri href="/job/{name}">Open</a>'
                + (f'<a class=btn href="/job/{name}?tab=review">Review page</a>' if j["ready"] else "")
                + "</div></div>"
            )
        parts.append("</div>")
    parts.append("</main></body></html>")
    return "".join(parts)


def _list_files(d: Path) -> str:
    items = []
    for f in sorted(d.rglob("*")):
        if f.is_file() and "_ve_work" not in f.parts:
            rel = f.relative_to(CONTENT_ROOT)
            items.append(
                f'<div class=fileitem>📄 <a href="/files/{_esc(str(rel))}" target=_blank>{_esc(f.name)}</a>'
                f'<span class=sz>{_human(f.stat().st_size)}</span></div>'
            )
    return "".join(items) or '<div class=meta>No files.</div>'


@app.get("/job/{name}", response_class=HTMLResponse)
async def job_page(name: str, tab: str = "outputs"):
    safe = Path(name).name
    ready = READY / safe
    staged = UPLOAD / safe
    parts = [_head(safe)]
    parts.append("<div class=tabs>"
                 f'<a href="/job/{_esc(safe)}" class={"on" if tab=="outputs" else ""}>Outputs</a>'
                 f'<a href="/job/{_esc(safe)}?tab=review" class={"on" if tab=="review" else ""}>Review page</a>'
                 f'<a href="/job/{_esc(safe)}?tab=copy" class={"on" if tab=="copy" else ""}>Post copy</a>'
                 f'<a href="/job/{_esc(safe)}?tab=logs" class={"on" if tab=="logs" else ""}>Logs</a>'
                 "</div>")

    if tab == "review":
        review = None
        if ready.is_dir():
            cands = list(ready.rglob("review.html"))
            review = cands[0] if cands else None
        if review:
            rel = review.relative_to(CONTENT_ROOT)
            parts.append(f'<iframe src="/files/{_esc(str(rel))}"></iframe>')
        else:
            parts.append("<div class=card>No review.html yet — it appears when the edit finishes.</div>")

    elif tab == "copy":
        packs = list(ready.rglob("*.postpack.json")) if ready.is_dir() else []
        if packs:
            try:
                data = json.loads(packs[0].read_text(encoding="utf-8"))
                pretty = json.dumps(data, indent=2, ensure_ascii=False)
            except Exception:
                pretty = packs[0].read_text(encoding="utf-8", errors="replace")
            parts.append(f"<pre>{_esc(pretty)}</pre>")
        else:
            parts.append("<div class=card>No post pack yet.</div>")

    elif tab == "logs":
        log = ready / "pipeline.log" if ready.is_dir() else None
        if log and log.exists():
            parts.append(f"<pre>{_esc(log.read_text(encoding='utf-8', errors='replace')[-20000:])}</pre>")
        else:
            parts.append("<div class=card>No log yet.</div>")

    else:  # outputs
        if ready.is_dir():
            vids = [f for f in sorted(ready.rglob("*.mp4")) if "_ve_work" not in f.parts]
            if vids:
                parts.append("<h2 class=sec>🎥 Videos (02_READY)</h2>")
                for v in vids:
                    rel = v.relative_to(CONTENT_ROOT)
                    parts.append(f"<div class=card style=margin-bottom:14px><div class=meta>{_esc(v.name)} · {_human(v.stat().st_size)}</div>"
                                 f'<video src="/files/{_esc(str(rel))}" controls preload=metadata></video></div>')
            parts.append("<h2 class=sec>All files</h2>" + _list_files(ready))
        if staged.is_dir():
            parts.append("<h2 class=sec>📤 Staged for Postiz (03_POSTIZ_UPLOAD)</h2>" + _list_files(staged))

    parts.append(
        f'<div class=row style=margin-top:24px>'
        f'<form method=post action="/job/{_esc(safe)}/retry"><button class=btn>↻ Re-edit (clear status, re-run)</button></form>'
        f"</div></main></body></html>"
    )
    return "".join(parts)


@app.post("/job/{name}/retry")
async def job_retry(name: str):
    safe = Path(name).name
    d = READY / safe
    for suffix in (".done", ".failed"):
        p = d / f"{safe}{suffix}"
        if p.exists():
            p.unlink()
    # If the raw file was consumed/renamed, keep it — autopilot rescans every 30s.
    return RedirectResponse(f"/job/{safe}", status_code=303)


@app.post("/upload")
async def upload(file: UploadFile = File(...), brief: str = Form(""), platforms: str = Form("")):
    name = Path(file.filename or "upload.mp4").name
    if Path(name).suffix.lower() not in VIDEO_EXT:
        return JSONResponse({"error": "not a video file"}, status_code=400)
    RAW.mkdir(parents=True, exist_ok=True)
    dest = RAW / name
    base, suf = dest.stem, dest.suffix
    i = 1
    while dest.exists():
        dest = RAW / f"{base}-{i}{suf}"
        i += 1
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    # studio job markers (content-studio watcher picks this up)
    (RAW / f"{dest.stem}.studio").write_text("web\n")
    if brief.strip():
        (RAW / f"{dest.stem}.brief.txt").write_text(brief.strip(), encoding="utf-8")
    if platforms.strip():
        (RAW / f"{dest.stem}.platforms.txt").write_text(platforms.strip(), encoding="utf-8")
    return {"queued": dest.name, "mode": "studio"}


@app.get("/files/{path:path}")
async def serve_file(path: str):
    p = (CONTENT_ROOT / path).resolve()
    if not str(p).startswith(str(CONTENT_ROOT.resolve())) or not p.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(p)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "4010")))
