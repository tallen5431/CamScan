"""Browser end-to-end test for where the job is stored.

The job used to live in localStorage (~5 MB per site). Each view stores two images of about
0.5 MB each — measured on a customer's real photos — so a job filled at about five photos,
fewer than one customer sent and fewer than a 3D part needs. It now lives in IndexedDB. This
pins the reason for that, and the risks that come with it:

  1. Capacity: an eight-view job of real-photo-sized images (~7 MB) survives a reload whole.
  2. Migration: a job an older version saved in localStorage loads, moves to IndexedDB, and
     leaves no stale copy behind to shadow it.
  3. Timing: IndexedDB writes finish later, and a write merely started while the page
     unloads may not land. So an edit on a reopened side is autosaved shortly after it is
     made, and the real "New" button waits for the save before reloading.

Not part of the fast smoke test (it needs Chromium + a running server). Run it directly:

    .venv/bin/python tests/test_storage_e2e.py

It SKIPS cleanly (exit 0) where Playwright or a browser isn't available, so it never breaks
an environment that can't run a browser — unless CAMSCAN_E2E_REQUIRE=1, as CI sets it.
"""
import os
import sys
import json
import time
import base64
import tempfile
import subprocess
import urllib.request
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PORT = os.getenv("CAMSCAN_E2E_PORT", "8199")


def _skip(msg):
    # A skip is a pass by default, so a machine with no browser is never broken by this.
    # CI sets CAMSCAN_E2E_REQUIRE=1: there a skip means the browser layer went untested
    # while everything reported green — which it did, silently, until this was added.
    if os.getenv("CAMSCAN_E2E_REQUIRE") == "1":
        print(f"E2E_RESULT: FAIL (required but skipped: {msg})")
        sys.exit(3)
    print(f"E2E_RESULT: SKIP ({msg})")
    sys.exit(0)


try:
    import cv2
    import numpy as np
    from playwright.sync_api import sync_playwright
except Exception as e:
    _skip(f"missing dependency: {e}")

_CHROMIUM = None
for cand in (os.getenv("PLAYWRIGHT_CHROMIUM"), "/opt/pw-browsers/chromium"):
    if cand and os.path.exists(cand):
        _CHROMIUM = cand
        break

N_VIEWS = 8


def _photo(seed):
    """Photo-like texture: ~0.46 MB as base64 JPEG at 1600 px, close to the 0.51 MB measured
    on the customer's photos, with a dark square to trace so a side can be edited."""
    rng = np.random.default_rng(seed)
    n = cv2.GaussianBlur(rng.normal(128, 40, (1200, 1600, 3)).astype(np.float32), (0, 0), 1.2)
    img = np.clip(n, 0, 255).astype(np.uint8)
    cv2.rectangle(img, (600, 400), (1000, 800), (20, 20, 20), -1)
    _ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode()


def _view(label, raw, w=1600, h=1200):
    return {"label": label, "image": raw, "units": "mm",
            "scale": {"calibrated": True, "source": "manual line"}, "measurements": [],
            "restore": {"v": 1, "w": w, "h": h, "raw": raw,
                        "calib": {"markers": [], "image_size": {"width": w, "height": h},
                                  "marker_size_mm": None},
                        "ann": [], "units": "mm",
                        "manual": {"mmPerPx": 0.25, "homography": None, "ref": None}}}


def _bundle(views, name):
    path = os.path.join(tempfile.gettempdir(), f"storage-e2e-{name}.camscan.json")
    json.dump({"kind": "camscan.job", "version": 1, "id": f"storage-{name}",
               "createdAt": "2026-01-01T00:00:00Z", "brief": {"part": name}, "views": views},
              open(path, "w"))
    return path


def _wait_server(timeout=40):
    for _ in range(timeout * 2):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).status == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


STORED = """(async()=>{ await window.CalibJob.ready; const j = await window.CalibJob.persisted();
  if(!j) return null;
  return { n:(j.views||[]).length,
           withImages:(j.views||[]).filter(v=>v.image && v.restore && v.restore.raw).length,
           polys:(j.views||[]).map(v=>((v.restore&&v.restore.ann)||[]).filter(a=>a&&a.type==='polyline').length),
           bytes: JSON.stringify(j).length }; })()"""
OV = "document.querySelector('.cal-view').__overlay"


def _ready(pg):
    pg.wait_for_function("window.CalibJob && window.CalibJob.ready && document.querySelector('.landing-card, .cal-view')",
                         timeout=20000)
    pg.evaluate("window.CalibJob.ready")


def _load(pg, path):
    with pg.expect_file_chooser() as fc:
        pg.click("#cal-landing-load button")
    fc.value.set_files(path)
    pg.wait_for_selector("#cal-job-panel", timeout=15000)


def _open_side(pg, i):
    pg.locator("#cal-job-panel").get_by_role("button", name="Open").nth(i).click()
    pg.wait_for_function(
        f"(()=>{{var v=document.querySelector('.cal-view');return v&&v.getAttribute('data-view-index')==='{i}'"
        "&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()", timeout=20000)


def main():
    raws = [_photo(i) for i in range(N_VIEWS)]
    big = _bundle([_view(f"View {i + 1}", r) for i, r in enumerate(raws)], "big")
    env = dict(os.environ, PORT=PORT, HOST="127.0.0.1")
    srv = subprocess.Popen([sys.executable, "app.py"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not _wait_server():
            print("E2E_RESULT: FAIL (server did not start)")
            return 1
        with sync_playwright() as p:
            try:
                launch = {"args": ["--no-proxy-server", "--no-sandbox"]}
                if _CHROMIUM:
                    launch["executable_path"] = _CHROMIUM
                browser = p.chromium.launch(**launch)
            except Exception as e:
                _skip(f"cannot launch chromium: {e}")
            errors = []

            # 1. Capacity: eight photo-sized views survive a reload.
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("dialog", lambda d: d.accept())
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            _ready(pg)
            store = pg.evaluate("window.CalibJob.storage()")
            assert store == "indexeddb", f"job is not in IndexedDB: {store}"
            _load(pg, big)
            pg.evaluate("window.CalibJob.flush()")
            pg.reload(wait_until="networkidle")
            _ready(pg)
            s1 = pg.evaluate(STORED)
            assert s1 and s1["n"] == N_VIEWS and s1["withImages"] == N_VIEWS, f"views lost: {s1}"
            in_mem = pg.evaluate("window.CalibJob.count()")
            assert in_mem == N_VIEWS, f"only {in_mem} of {N_VIEWS} views loaded after reload"
            mb = s1["bytes"] / 1e6

            # 3a. Autosave: trace on a reopened side, touch nothing else, and it is stored.
            pg.evaluate("window.CalibJob.open()")
            _open_side(pg, 2)
            pg.evaluate(f"(()=>{{const o={OV};o.setMode('outline');o.autoOutline([800,600]);}})()")
            pg.wait_for_function(f"{OV}.ann.items.some(a=>a.type==='polyline'&&a.closed)", timeout=20000)
            deadline = time.time() + 8
            polys = None
            while time.time() < deadline:
                polys = pg.evaluate(STORED)["polys"]
                if polys[2] >= 1:
                    break
                time.sleep(0.4)
            assert polys and polys[2] >= 1, f"edit on a reopened side was not autosaved: {polys}"

            # 3b. The real New button: a second edit made just before pressing it survives.
            #     Added the way the tracer adds one (history push, then the annotation), and the
            #     New button is pressed immediately — well inside the autosave delay — so only
            #     the reload path can have saved it.
            pg.evaluate(f"(()=>{{const o={OV};o._pushHistory();"
                        "window.CalibAnn.addPolyline(o.ann,[[200,200],[400,200],[400,400],[200,400]],o.getScale()||0.25,o.opts.units,null,true);"
                        "o.requestDraw();})()")
            with pg.expect_navigation(timeout=20000):
                pg.locator('#cal-tb-new').click()
            _ready(pg)
            s2 = pg.evaluate(STORED)
            assert s2["polys"][2] >= 2, f"edit just before New was lost: {s2['polys']}"
            assert s2["n"] == N_VIEWS, f"views lost across New: {s2}"
            ctx.close()

            # 2. Migration from a job an older version left in localStorage.
            ctx = browser.new_context()
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            _ready(pg)
            small = _photo(99)
            legacy = {"id": "legacy", "createdAt": "2025-01-01T00:00:00Z", "brief": {"part": "legacy"},
                      "views": [_view("Old A", small), _view("Old B", small)]}
            pg.evaluate("(j)=>{ localStorage.setItem('camscan.job.v1', JSON.stringify(j)); }", legacy)
            pg.reload(wait_until="networkidle")
            _ready(pg)
            pg.evaluate("window.CalibJob.flush()")
            mig = pg.evaluate("""(async()=>{ const j = await window.CalibJob.persisted();
                return { n: j ? j.views.length : 0, part: j && j.brief && j.brief.part,
                         left: localStorage.getItem('camscan.job.v1') !== null,
                         store: window.CalibJob.storage() }; })()""")
            assert mig == {"n": 2, "part": "legacy", "left": False, "store": "indexeddb"}, f"migration: {mig}"
            ctx.close()

            assert not errors, f"page errors: {errors}"
            browser.close()
        print(f"E2E_RESULT: PASS ({N_VIEWS} views / {mb:.1f} MB survive reload in IndexedDB; "
              f"reopened-side edit autosaved; edit before New kept; legacy localStorage job migrated)")
        return 0
    except AssertionError as e:
        print(f"E2E_RESULT: FAIL ({e})")
        return 1
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())
