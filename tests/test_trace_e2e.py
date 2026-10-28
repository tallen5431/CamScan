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


def _tidy(pg, item):
    """The clean-up tools live in the ✨ Tidy menu: open it, then choose."""
    pg.click("#cal-ctx-tidy")
    pg.locator(item).wait_for(state="visible", timeout=5000)
    pg.click(item)


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

            # Handles are a CONSTANT on-screen size: _handleR() is in image units, so
            # _handleR()*vp.k (its on-screen size) must be invariant across zoom — zooming in
            # yields a SMALLER image-space dot, i.e. precise placement over the edge.
            hsize = pg.evaluate(
                "(()=>{var o=document.querySelector('.cal-view').__overlay;var k1=o.vp.k;"
                "var r1=o._handleR(), l1=o._lblScale(), w1=o._lineW();"
                "o.vp.setZoomAround(k1*3, o.vp.centerAnchor());var k2=o.vp.k;"
                "var r2=o._handleR(), l2=o._lblScale(), w2=o._lineW();o.redraw();"
                "return {screen1:r1*k1, screen2:r2*k2, shrank:r2<r1, lbl1:l1*k1, lbl2:l2*k2, ln1:w1*k1, ln2:w2*k2};})()")
            assert abs(hsize["screen1"] - hsize["screen2"]) < 1e-6 and hsize["shrank"], f"handles not constant on-screen: {hsize}"
            # Labels and lines too: sized in image pixels they grew with zoom until a label
            # covered the dots being checked and a selected line hid the edge (customer report).
            assert abs(hsize["lbl1"] - hsize["lbl2"]) < 1e-6 and abs(hsize["ln1"] - hsize["ln2"]) < 1e-6, \
                f"labels/lines not constant on-screen: {hsize}"
            # Hiding measurement labels must redraw cleanly (lets you edit a dense outline).
            pg.evaluate("(()=>{var o=document.querySelector('.cal-view').__overlay;o.opts.showLabels=false;o.redraw();o.opts.showLabels=true;o.redraw();})()")

            # Refine on the outline exactly as traced re-traces it at full detail first (the
            # first trace's simplification loses gentle curves). The disc has no straight sides,
            # so the full-detail trace itself is the result.
            o_before = pg.evaluate(OUTER)
            pg.evaluate(f"(()=>{{const o=document.querySelector('.cal-view').__overlay;o.ann.selectedId={o_before['id']};o.setMode('select');o.requestDraw();}})()")
            pg.locator("#cal-ctx-tidy").wait_for(state="visible", timeout=5000)
            with pg.expect_request(lambda q: "/api/trace" in q.url, timeout=20000) as tq:
                _tidy(pg, "#cal-ctx-refine")
            assert json.loads(tq.value.post_data).get("simplify") == 0.0003, "Refine did not re-trace at full detail"
            pg.wait_for_function("[...document.querySelectorAll('div')].some(d=>d.innerText && d.innerText.startsWith('Refined: re-traced at full detail'))",
                                 timeout=20000)
            pg.evaluate("document.querySelector('.cal-view').__overlay.undo()")

            # Straighten. The disc is round: the button must leave it exactly as it is.
            O = "document.querySelector('.cal-view').__overlay"
            before = pg.evaluate(OUTER)
            pg.evaluate(f"(()=>{{const o={O};o.ann.selectedId={before['id']};o.setMode('select');o.requestDraw();}})()")
            pg.locator("#cal-ctx-tidy").wait_for(state="visible", timeout=5000)
            with pg.expect_response(lambda r: "/api/outline/straighten" in r.url):
                _tidy(pg, "#cal-ctx-straighten")
            time.sleep(0.3)
            assert pg.evaluate(OUTER)["pts"] == before["pts"], "a round outline was changed by Straighten"

            #    A square traced with wobbly dots becomes four straight sides; Undo restores it.
            sq = pg.evaluate(f"""(()=>{{const o={O};const pts=[];let s=7;
                const r=()=>{{s=(s*16807)%2147483647;return (s/2147483647-0.5)*6;}};
                const side=(x0,y0,x1,y1)=>{{for(let i=0;i<12;i++){{const t=i/12;pts.push([x0+(x1-x0)*t+r(),y0+(y1-y0)*t+r()]);}}}};
                side(60,60,260,60);side(260,60,260,260);side(260,260,60,260);side(60,260,60,60);
                o._pushHistory();window.CalibAnn.addPolyline(o.ann,pts,o.getScale()||0.1,o.opts.units,null,true);
                o.setMode('select');o.requestDraw();return {{id:o.ann.selectedId,n:pts.length}};}})()""")
            with pg.expect_response(lambda r: "/api/outline/straighten" in r.url):
                _tidy(pg, "#cal-ctx-straighten")
            sel = f"{O}.ann.items.find(a=>a.id==={sq['id']})"
            pg.wait_for_function(f"{sel}.pts.length<10", timeout=5000)
            after = pg.evaluate(f"{sel}.pts")
            xs = sorted(p[0] for p in after)
            assert len(after) == 4 and xs[1] - xs[0] < 6 and xs[3] - xs[2] < 6, f"square not straightened: {after}"
            pg.evaluate(f"{O}.undo()")
            back = pg.evaluate(f"{sel}.pts.length")
            assert back == sq["n"], f"Undo did not restore the traced dots: {back} vs {sq['n']}"

            #    Refine: the square's corners are sharp in the photo (there is no edge there at
            #    all), so it comes back as four sides and four sharp corners, one undo step.
            pg.evaluate(f"(()=>{{const o={O};o.ann.selectedId={sq['id']};o.setMode('select');o.requestDraw();}})()")
            pg.locator("#cal-ctx-tidy").wait_for(state="visible", timeout=5000)
            with pg.expect_response(lambda r: "/api/outline/straighten" in r.url) as rr:
                _tidy(pg, "#cal-ctx-refine")
            assert json.loads(rr.value.request.post_data).get("mode") == "refine", "Refine did not ask for refine"
            pg.wait_for_function(f"{sel}.pts.length<10", timeout=5000)
            assert len(pg.evaluate(f"{sel}.pts")) == 4, "Refine rounded a corner the photo does not show"
            pg.evaluate(f"{O}.undo()")
            assert pg.evaluate(f"{sel}.pts.length") == sq["n"], "Undo after Refine did not restore the dots"

            # Symmetric: a lopsided version of a symmetric shape comes back symmetric; the
            # message offers the other axis, which is applied to the outline as it was.
            sy = pg.evaluate(f"""(()=>{{const o={O};o._pushHistory();
                window.CalibAnn.addPolyline(o.ann,[[150,60],[450,60],[450,170],[330,170],[300,215],[200,205],[170,165],[150,165]],o.getScale()||0.1,o.opts.units,null,true);
                o.setMode('select');o.requestDraw();return o.ann.selectedId;}})()""")
            with pg.expect_response(lambda r: "/api/outline/symmetric" in r.url) as yr:
                _tidy(pg, "#cal-ctx-symmetric")
            yj = yr.value.json()
            assert yj.get("ok") and yj.get("axis") in ("long", "short"), f"symmetric refused: {yj}"
            pg.wait_for_function(f"{O}.ann.items.find(a=>a.id==={sy}).pts.length !== 8", timeout=5000)
            with pg.expect_response(lambda r: "/api/outline/symmetric" in r.url) as yr2:
                pg.locator(".cal-toast-action", has_text="Use the").click()
            assert yr2.value.json().get("axis") == yj["other"]["axis"], "other axis not applied"
            assert json.loads(yr2.value.request.post_data)["points"] != pg.evaluate(f"{O}.ann.items.find(a=>a.id==={sy}).pts"), \
                "the other axis was applied to the already-symmetric outline, not the original"

            # Square up: a scale line along a "ruler", and an outline whose long sides converge
            # (a board shot at an angle). The button turns the line scale into a tilt-corrected
            # one; the chip says so; Undo puts the line scale back.
            pg.evaluate(f"""(()=>{{const o={O};
                o.setManualScaleFromPixels(200, 50, [[300,150],[300,350]]);
                o._pushHistory();
                window.CalibAnn.addPolyline(o.ann,[[200,40],[400,40],[420,460],[180,460]],o.getScale(),o.opts.units,null,true);
                o.setMode('select');o.requestDraw();}})()""")
            pg.locator("#cal-ctx-tidy").wait_for(state="visible", timeout=5000)
            with pg.expect_response(lambda r: "/api/square_up" in r.url) as sr:
                _tidy(pg, "#cal-ctx-squareup")
            assert sr.value.json().get("ok"), f"square up refused: {sr.value.json()}"
            pg.wait_for_function(f"{O}.opts.manualHomography && {O}._manualRef && {O}._manualRef.kind==='squared'",
                                 timeout=5000)
            pg.wait_for_function("document.getElementById('cal-tb-scale').innerText.includes('squared')", timeout=5000)
            pg.locator(".cal-toast-action", has_text="Undo").click()
            pg.wait_for_function(f"!{O}.opts.manualHomography && {O}.opts.manualMmPerPx>0", timeout=5000)

            assert not errors, f"page errors: {errors}"
            browser.close()
        print(f"E2E_RESULT: PASS (outer polyline + true-circle bore; slider re-traces coarse={coarse}->fine={fine} pts, replaced; "
              f"Straighten leaves a round part alone, makes a wobbly square 4 sides, Undo restores; Refine keeps sharp corners sharp; Symmetric + other axis; Square up applies and undoes)")
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
