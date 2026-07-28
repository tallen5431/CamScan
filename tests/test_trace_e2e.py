"""Browser end-to-end for the auto-trace detail slider + true-circle holes.

Boots the app, loads a part image with a round bore, runs the auto-trace, and asserts:
  * the outer boundary comes in as a closed polyline AND the round hole as a TRUE circle
    annotation (perfect bore in the DXF), plus the Detail slider appears;
  * dragging the slider re-traces the SAME spot — finer yields more outer vertices, and it
    REPLACES the previous outline+hole (no stacking).

Not part of the fast smoke test (needs Chromium + a running server). Run directly:
    .venv/bin/python tests/test_trace_e2e.py
Skips cleanly (exit 0) where Playwright / a browser isn't available.
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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8198")


def _skip(msg):
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


def _bundle_file():
    # A dark disc with a round bore (background shows through) on a light wall.
    img = np.full((500, 700, 3), 185, np.uint8)
    cv2.circle(img, (350, 250), 150, (55, 58, 62), -1)
    cv2.circle(img, (350, 250), 70, (185, 185, 185), -1)
    _ok, enc = cv2.imencode(".jpg", img)
    raw = "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode()
    bundle = {
        "kind": "camscan.job", "version": 1, "id": "trace-e2e", "createdAt": "2026-01-01T00:00:00Z",
        "brief": {"part": "bore disc"},
        "views": [{
            "label": "Top", "image": raw, "units": "mm",
            "scale": {"calibrated": True, "source": "manual line"}, "measurements": [],
            "restore": {"v": 1, "w": 700, "h": 500, "raw": raw,
                        "calib": {"markers": [], "image_size": {"width": 700, "height": 500}, "marker_size_mm": None},
                        "ann": [], "units": "mm", "manual": {"mmPerPx": 0.1, "homography": None, "ref": None}},
        }],
    }
    path = os.path.join(tempfile.gettempdir(), "trace-e2e.camscan.json")
    json.dump(bundle, open(path, "w"))
    return path


def _wait_server(timeout=40):
    for _ in range(timeout * 2):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).status == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


OUTER = "(()=>{var p=document.querySelector('.cal-view').__overlay.ann.items.filter(a=>a.type==='polyline');return p.length?p[0]:null;})()"


def main():
    bundle_path = _bundle_file()
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
            pg = browser.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)

            # Load the bundle and open the side into an editable overlay.
            with pg.expect_file_chooser() as fc:
                pg.click("#cal-landing-load button")
            fc.value.set_files(bundle_path)
            pg.wait_for_selector("#cal-job-panel", timeout=8000)
            pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()
            pg.wait_for_function(
                "(()=>{var v=document.querySelector('.cal-view');return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()",
                timeout=15000)

            # Run the auto-trace on the disc's ring (seed off the hole).
            pg.evaluate("(()=>{var o=document.querySelector('.cal-view').__overlay;o.setMode('outline');o.autoOutline([350,120]);})()")
            pg.wait_for_function(
                "(()=>{var o=document.querySelector('.cal-view').__overlay;var it=o.ann.items;"
                "return it.some(a=>a.type==='polyline'&&a.closed)&&it.some(a=>a.type==='circle')"
                "&&document.getElementById('cal-trace-detail');})()", timeout=20000)

            shot = pg.evaluate(
                "(()=>{var it=document.querySelector('.cal-view').__overlay.ann.items;"
                "var poly=it.filter(a=>a.type==='polyline'),circ=it.filter(a=>a.type==='circle');"
                "return {polys:poly.length,circs:circ.length,cx:circ[0]&&circ[0].center[0],cy:circ[0]&&circ[0].center[1],r:circ[0]&&circ[0].radius};})()")
            assert shot["polys"] == 1 and shot["circs"] == 1, f"expected 1 outline + 1 circle bore, got {shot}"
            assert abs(shot["cx"] - 350) < 25 and abs(shot["cy"] - 250) < 25 and abs(shot["r"] - 70) < 20, f"bore geometry off: {shot}"
            assert pg.query_selector("#cal-trace-detail") is not None, "detail slider missing"

            def outer_id():
                o = pg.evaluate(OUTER)
                return o["id"] if o else -1

            def outer_len():
                o = pg.evaluate(OUTER)
                return len(o["pts"]) if o else -1

            # Coarse: slider to 0 (simplify 0.003). Re-trace replaces the outline (new id).
            oid = outer_id()
            with pg.expect_response(lambda r: "/api/trace" in r.url):
                pg.eval_on_selector("#cal-trace-detail-rng", "el=>{el.value='0';el.dispatchEvent(new Event('change'));}")
            pg.wait_for_function(f"(()=>{{var p=document.querySelector('.cal-view').__overlay.ann.items.filter(a=>a.type==='polyline');return p.length===1&&p[0].id!=={oid};}})()", timeout=20000)
            coarse = outer_len()

            # Fine: slider to 100 (simplify 0.0003). More outer vertices; still replaces.
            oid = outer_id()
            with pg.expect_response(lambda r: "/api/trace" in r.url):
                pg.eval_on_selector("#cal-trace-detail-rng", "el=>{el.value='100';el.dispatchEvent(new Event('change'));}")
            pg.wait_for_function(f"(()=>{{var p=document.querySelector('.cal-view').__overlay.ann.items.filter(a=>a.type==='polyline');return p.length===1&&p[0].id!=={oid};}})()", timeout=20000)
            fine = outer_len()

            final = pg.evaluate(
                "(()=>{var it=document.querySelector('.cal-view').__overlay.ann.items;"
                "return {polys:it.filter(a=>a.type==='polyline').length,circs:it.filter(a=>a.type==='circle').length};})()")
            assert fine > coarse, f"finer detail should add vertices: coarse={coarse}, fine={fine}"
            assert final["polys"] == 1 and final["circs"] == 1, f"re-trace must replace, not stack: {final}"
            assert not errors, f"page errors: {errors}"
            browser.close()
        print(f"E2E_RESULT: PASS (outer polyline + true-circle bore; slider re-traces coarse={coarse}->fine={fine} pts, replaced)")
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
