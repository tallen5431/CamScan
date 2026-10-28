"""Browser end-to-end test for scale and job state that must survive edits, undo and reopening.

Each check below was a reproduced bug:
  - undo brought back each measurement's frozen mm/px from before a scale change;
  - a marker confirmed by hand, and the reference size behind the "reference too small"
    warning, were lost when a side was reopened;
  - a paper-rectangle scale kept the superseded ruler line, so Square up rebuilt the scale
    from it;
  - a Square up reply that arrived after a new scale was set overwrote it, and its Undo
    button (on screen for 16 s) put back a scale from before the new one;
  - a dragged dot or a new scale on a reopened side was autosaved only at unload;
  - every side opened left a document click and job-changed listener behind, each holding a
    dead overlay and its photo.

/api/square_up is answered by the test (route), so its timing can be controlled.

    .venv/bin/python tests/test_state_e2e.py

It SKIPS cleanly (exit 0) where Playwright or a browser isn't available — unless
CAMSCAN_E2E_REQUIRE=1, as CI sets it.
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
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PORT = os.getenv("CAMSCAN_E2E_PORT", "8207")


def _skip(msg):
    if os.getenv("CAMSCAN_E2E_REQUIRE") == "1":
        print(f"E2E_RESULT: FAIL (required but skipped: {msg})")
        sys.exit(3)
    print(f"E2E_RESULT: SKIP ({msg})")
    sys.exit(0)


try:
    import cv2
    import numpy as np
    from playwright.sync_api import sync_playwright
except Exception as e:  # pragma: no cover - environment without deps
    _skip(f"missing dependency: {e}")

_CHROMIUM = None
for cand in (os.getenv("PLAYWRIGHT_CHROMIUM"), "/opt/pw-browsers/chromium"):
    if cand and os.path.exists(cand):
        _CHROMIUM = cand
        break

O = "document.querySelector('.cal-view').__overlay"
H_SQ = [[0.11, 0.01, 0], [0, 0.12, 0], [0, 0.0001, 1]]


def _bundle():
    img = np.full((300, 400, 3), 180, np.uint8)
    cv2.rectangle(img, (60, 60), (320, 240), (60, 62, 66), -1)
    _ok, enc = cv2.imencode(".jpg", img)
    raw = "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode()

    def view(label, calib, ann, manual):
        return {"label": label, "image": raw, "units": "mm", "scale": {"calibrated": True},
                "measurements": [], "restore": {"v": 1, "w": 400, "h": 300, "raw": raw, "calib": calib,
                                                "ann": ann, "units": "mm", "manual": manual}}
    plain = {"markers": [], "image_size": {"width": 400, "height": 300}}
    unconfirmed = {"markers": [{"id": 1, "corners": [{"x": 10, "y": 10}, {"x": 50, "y": 10}, {"x": 50, "y": 50},
                                                     {"x": 10, "y": 50}], "edge_px": 40, "mm_per_px": 0.5}],
                   "image_size": {"width": 400, "height": 300}, "marker_size_mm": 20, "calibration_usable": False}
    outline = [{"id": 7, "type": "polyline", "pts": [[60, 60], [320, 60], [320, 240], [60, 240]], "closed": True,
                "mm_per_px": 0.1, "units": "mm", "markerId": None}]
    line = {"mmPerPx": 0.1, "homography": None, "ref": None, "line": {"a": [60, 250], "b": [160, 250], "mm": 10}}
    none = {"mmPerPx": None, "homography": None, "ref": None}
    b = {"kind": "camscan.job", "version": 1, "id": "job-state", "createdAt": "2026-01-01T00:00:00Z",
         "brief": {"part": "x"},
         "views": [view("Top", plain, outline, line), view("Side", unconfirmed, [], none)]}
    path = os.path.join(tempfile.gettempdir(), "e2e-state.camscan.json")
    json.dump(b, open(path, "w"))
    return path


def _wait_server(timeout=40):
    for _ in range(timeout * 2):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).status == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


def _open_side(pg, i):
    pg.evaluate("()=>{ if(!document.getElementById('cal-job-panel')) window.CalibJob.open(); }")
    pg.evaluate(f"()=>{{ const b=[...document.querySelectorAll('#cal-job-panel button')]"
                f".filter(x=>x.textContent==='Open'); b[{i}].click(); }}")
    pg.wait_for_function(f"(()=>{{const v=document.querySelector('.cal-view'); return v && "
                         f"v.getAttribute('data-view-index')==='{i}' && v.__overlay && v.__overlay._gestureDetach; }})()",
                         timeout=15000)
    time.sleep(0.4)


def main():
    path = _bundle()
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
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            pg = ctx.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            delay = {"s": 0.0}

            def sq_route(route):
                time.sleep(delay["s"])
                route.fulfill(status=200, content_type="application/json", body=json.dumps(
                    {"ok": True, "H": H_SQ, "tilt_deg": 12.3, "reference": "part", "focal_source": "exif",
                     "sides": [], "ruler_edges": None, "width_change_pct": 4, "other": None}))
            pg.route("**/api/square_up", sq_route)
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
            with pg.expect_file_chooser() as fc:
                pg.click("#cal-landing-load button")
            fc.value.set_files(path)
            pg.wait_for_selector("#cal-job-panel", timeout=8000)
            _open_side(pg, 0)

            # 1. Undo after a scale change reads the CURRENT scale, not the one frozen then.
            r = pg.evaluate(f"""()=>{{ const o={O};
                o.setManualScaleFromPixels(100, 10, [[0,0],[100,0]]);
                o._pushHistory(); window.CalibAnn.addSegment(o.ann,[0,100],[100,100],o.getScale(),'mm',null);
                o._pushHistory(); window.CalibAnn.addSegment(o.ann,[0,150],[100,150],o.getScale(),'mm',null);
                o.setManualScaleFromPixels(100, 20, [[0,0],[100,0]]);
                o.undo();
                const seg=o.ann.items.find(a=>a.type==='segment');
                return {{scale:o.getScale(), seg:seg.mm_per_px}}; }}""")
            assert abs(r["seg"] - r["scale"]) < 1e-12, f"undo brought back a stale per-item scale: {r}"
            pg.evaluate(f"()=>{{ const o={O}; o.ann.items=o.ann.items.filter(a=>a.type!=='segment'); "
                        "o.setManualScaleFromPixels(100, 10, [[60,250],[160,250]]); }")

            # 2. A paper rectangle replaces the ruler line: Square up must not rebuild from it.
            r = pg.evaluate(f"""()=>{{ const o={O};
                o._applyRectScale([[50,50],[350,50],[350,280],[50,280]], 210, 297);
                return {{line:o._scaleLine, src:o.getScaleInfo().source}}; }}""")
            assert r["line"] is None and r["src"] == "paper/rectangle", f"rectangle kept the old line: {r}"

            # 3. A Square up reply that lands after a new scale was set is refused.
            delay["s"] = 1.2
            r = pg.evaluate(f"""async ()=>{{ const o={O};
                o.setManualScaleFromPixels(100, 10, [[60,250],[160,250]]);
                o.ann.selectedId=7;
                const p=o.squareUp();
                await new Promise(r=>setTimeout(r,200));
                o.setManualScaleFromPixels(200, 50, [[10,10],[210,10]]);
                const ok=await p;
                return {{ok, mm:o.opts.manualMmPerPx, H:o.opts.manualHomography}}; }}""")
            assert r["ok"] is False and abs(r["mm"] - 0.25) < 1e-12 and r["H"] is None, f"stale reply applied: {r}"
            delay["s"] = 0.0

            # 4. Square up's Undo does nothing once another scale has been set.
            r = pg.evaluate(f"""async ()=>{{ const o={O};
                o.setManualScaleFromPixels(100, 10, [[60,250],[160,250]]);
                o.ann.selectedId=7;
                const ok=await o.squareUp();
                const applied=!!o.opts.manualHomography;
                o.setManualScaleFromPixels(200, 50, [[10,10],[210,10]]);
                const undo=[...document.querySelectorAll('.cal-toast-action')].find(b=>b.textContent==='Undo');
                if(undo) undo.click();
                return {{ok, applied, found:!!undo, mm:o.opts.manualMmPerPx}}; }}""")
            assert r["ok"] and r["applied"] and r["found"] and abs(r["mm"] - 0.25) < 1e-12, \
                f"a stale Square up Undo changed the newer scale: {r}"

            # 5. Autosave: a drag and a new scale reach storage without an unload.
            _open_side(pg, 0)
            time.sleep(1.5)
            pt = pg.evaluate(f"""()=>{{ const o={O}; const r=o.canvas.getBoundingClientRect();
                const c=o.vp.imageToCanvas(320,240); const s=o.canvas.width/r.width;
                return [r.left+c[0]/s, r.top+c[1]/s]; }}""")
            pg.mouse.move(pt[0], pt[1])
            pg.mouse.down()
            pg.mouse.move(pt[0] + 40, pt[1] + 30, steps=8)
            pg.mouse.up()
            pg.evaluate(f"()=>{O}.setManualScaleFromPixels(100, 40, [[0,0],[100,0]])")
            deadline = time.time() + 6
            while True:
                r = pg.evaluate(f"""async ()=>{{ const o={O}; const s=await window.CalibJob.persisted();
                    const sv=s.views[0].restore, live=o.ann.items.find(a=>a.id===7);
                    return {{live:live.pts[2], stored:sv.ann.find(a=>a.id===7).pts[2], mm:sv.manual.mmPerPx}}; }}""")
                if (r["live"] == r["stored"] and abs((r["mm"] or 0) - 0.4) < 1e-9) or time.time() > deadline:
                    break
                time.sleep(0.5)
            assert r["live"] == r["stored"] and abs((r["mm"] or 0) - 0.4) < 1e-9, f"not autosaved: {r}"

            # 6. A confirmed marker and the reference-size warning survive reopening.
            _open_side(pg, 1)
            r = pg.evaluate(f"()=>{{ const o={O}; const pre=o.isCalibrated(); o.confirmDetectedMarker(20); "
                            "return {pre, post:o.isCalibrated()}; }")
            assert r == {"pre": False, "post": True}, f"confirm did not calibrate: {r}"
            pg.evaluate("()=>window.CalibJob.syncOpen()")
            _open_side(pg, 0)
            pg.evaluate(f"""()=>{{ const o={O}; o.ann.items=[];
                o.setManualScaleFromPixels(10, 5, [[0,0],[10,0]]);
                o._pushHistory(); window.CalibAnn.addSegment(o.ann,[0,100],[390,100],o.getScale(),'mm',null);
                window.CalibJob.syncOpen(); }}""")
            _open_side(pg, 1)
            side = pg.evaluate(f"()=>{O}.isCalibrated()")
            _open_side(pg, 0)
            warn = pg.evaluate(f"()=>{{ const w={O}.referenceSpanWarning(); return w && w.level; }}")
            assert side is True, "a confirmed marker was lost on reopening"
            assert warn == "bad", f"the too-small-reference warning was lost on reopening: {warn}"

            # 7. Opening sides does not pile up document listeners.
            cdp = ctx.new_cdp_session(pg)

            def listeners():
                d = cdp.send("Runtime.evaluate", {"expression": "document"})
                ls = cdp.send("DOMDebugger.getEventListeners", {"objectId": d["result"]["objectId"]})["listeners"]
                return Counter(x["type"] for x in ls)
            before = listeners()
            for i in (1, 0, 1, 0):
                _open_side(pg, i)
            after = listeners()
            grew = {k: (before.get(k, 0), v) for k, v in after.items() if v > before.get(k, 0)}
            assert not grew, f"document listeners grew while switching sides: {grew}"

            assert not errors, f"page errors: {errors}"
            browser.close()
        print("E2E_RESULT: PASS (undo reads the current scale; rectangle drops the old line; late Square up "
              "refused and stale Undo inert; drag + scale autosaved; confirmed marker + reference warning "
              "survive reopen; no listener build-up)")
        return 0
    except AssertionError as e:
        print(f"E2E_RESULT: FAIL ({e})")
        return 1
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()


if __name__ == "__main__":
    sys.exit(main())
