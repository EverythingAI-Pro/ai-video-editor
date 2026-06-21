#!/usr/bin/env python3
"""Headless Chrome CDP capture with consent-banner click. Usage: cdp_capture.py URL OUT.png [extra_wait_s]"""
import asyncio, base64, json, subprocess, sys, time, urllib.request, tempfile, shutil, os, signal

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9777

CLICK_JS = r"""
(() => {
  const rx = /accept all|accept cookies|accepteren|alles accepteren|tout accepter|consentir|agree|akkoord|got it|allow all|j'accepte|i accept|^accept$|^agree$/i;
  function tryClick(root) {
    const els = root.querySelectorAll('button, a, [role=button], input[type=button], input[type=submit]');
    for (const el of els) {
      const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();
      if (t && rx.test(t)) { el.click(); return t; }
    }
    return null;
  }
  let r = tryClick(document);
  if (r) return 'clicked:' + r;
  // try shadow roots
  for (const el of document.querySelectorAll('*')) {
    if (el.shadowRoot) { const s = tryClick(el.shadowRoot); if (s) return 'clicked-shadow:' + s; }
  }
  return 'none';
})()
"""

async def main():
    url, out = sys.argv[1], sys.argv[2]
    extra = float(sys.argv[3]) if len(sys.argv) > 3 else 0
    import websockets
    prof = tempfile.mkdtemp()
    proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={prof}", "--hide-scrollbars", "--window-size=1920,1080",
        "--disable-gpu", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws_url = None
        for _ in range(40):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                page = [t for t in tabs if t.get("type") == "page"]
                if page: ws_url = page[0]["webSocketDebuggerUrl"]; break
            except Exception: pass
            time.sleep(0.5)
        if not ws_url: print("ERROR: no debugger"); return 1
        async with websockets.connect(ws_url, max_size=50*1024*1024) as ws:
            mid = 0
            async def cmd(method, **params):
                nonlocal mid; mid += 1
                await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == mid: return m.get("result", {})
            contexts = []
            async def cmd_collect(method, **params):
                nonlocal mid; mid += 1
                await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("method") == "Runtime.executionContextCreated":
                        contexts.append(m["params"]["context"]["id"])
                    if m.get("id") == mid: return m.get("result", {})
            await cmd("Page.enable")
            await cmd_collect("Runtime.enable")
            import os as _os
            _dsf = int(_os.environ.get("CDP_SCALE", "1"))
            await cmd("Emulation.setDeviceMetricsOverride", width=1920, height=1080, deviceScaleFactor=_dsf, mobile=False)
            await cmd_collect("Page.navigate", url=url)
            # drain events while waiting
            end = time.time() + 7 + extra
            while time.time() < end:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
                    if m.get("method") == "Runtime.executionContextCreated":
                        contexts.append(m["params"]["context"]["id"])
                except asyncio.TimeoutError: pass
            # click consent in EVERY execution context (main + iframes)
            for ctx in [None] + contexts:
                try:
                    kw = {"expression": CLICK_JS, "returnByValue": True}
                    if ctx: kw["contextId"] = ctx
                    r = await cmd("Runtime.evaluate", **kw)
                    v = r.get("result", {}).get("value")
                    if v and v != "none": print(f"ctx {ctx}:", v)
                except Exception: pass
            await asyncio.sleep(2.5)
            # fallback: remove CMP overlays (sourcepoint/onetrust/etc) from main frame
            await cmd("Runtime.evaluate", expression=r"""
              (() => { let n=0;
                for (const sel of ["iframe[id^='sp_message']","[id*='sp_message']","[class*='sp_message']",
                                   "#onetrust-consent-sdk","#qc-cmp2-container","[id*='cmp-container']"]) {
                  document.querySelectorAll(sel).forEach(e => { e.remove(); n++; });
                }
                document.querySelectorAll('div').forEach(e => {
                  const s = getComputedStyle(e);
                  if ((s.position==='fixed'||s.position==='sticky') && parseInt(s.zIndex||0) > 100000 &&
                      e.offsetWidth >= innerWidth*0.5 && e.offsetHeight >= innerHeight*0.3) { e.remove(); n++; }
                });
                document.body.style.overflow='auto'; document.documentElement.style.overflow='auto';
                return 'removed:'+n; })()
            """, returnByValue=True)
            await asyncio.sleep(1.0)
            shot = await cmd("Page.captureScreenshot", format="png")
            with open(out, "wb") as f: f.write(base64.b64decode(shot["data"]))
            print("saved", out)
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
        shutil.rmtree(prof, ignore_errors=True)
    return 0

sys.exit(asyncio.run(main()))
