"""Browser end-to-end: a re-trace keeps the outline's link to its 3D view, and the trace
flow round it.

  1. Trace a part, assign it as the Top view, move the Detail slider, then change the scale.
     The re-trace used to give the outline a new id, so the view lost it: it kept the old
     copy, ignored the new scale and said its outline was deleted. Now the view keeps its
     ann_id, has no orphan flag, and its size follows the scale.
  2. Tap trace, draw an Area box round the part, tap again (the success message's own advice
     for a missed shadow): exactly one outline, and the box used as the trace region is gone;
     undo brings the box back as the user's measured rectangle.
  3. A trace that finds nothing says to draw an Area box, and the next tap traces again
     (it used to drop silently into placing hand-trace dots). An Area box elsewhere on the
     photo is not the trace region: it is not sent, and stays a measurement through a failed
     and a successful trace.
  4. A message about one photo goes when the photo does.
  5. Labels read to 0.1 mm; an unconfirmed marker candidate is neither drawn nor exported.

Not part of the fast smoke test (needs Chromium + a running server). Run directly:
    .venv/bin/python tests/test_retrace_link_e2e.py
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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8195")


def _skip(msg):
    # A skip is a pass by default, so a machine with no browser is never broken by this.
    # CI sets CAMSCAN_E2E_REQUIRE=1: there a skip means the browser layer went untested.
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


def _data_url(img):
    _ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode()


def _bundle_file():
    img = np.full((600, 800, 3), 190, np.uint8)
    cv2.rectangle(img, (250, 200), (550, 400), (70, 70, 70), -1)
    raw = _data_url(img)
    bundle = {
        "kind": "camscan.job", "version": 1, "id": "retrace-link-e2e", "createdAt": "2026-01-01T00:00:00Z",
        "brief": {"part": "plate"},
        "views": [{
            "label": "Top", "image": raw, "units": "mm",
            "scale": {"calibrated": True, "source": "manual line"}, "measurements": [],
            "restore": {"v": 1, "w": 800, "h": 600, "raw": raw,
                        "calib": {"markers": [], "image_size": {"width": 800, "height": 600}, "marker_size_mm": None},
                        "ann": [], "units": "mm", "manual": {"mmPerPx": 0.5, "homography": None, "ref": None}},
        }],
    }
    path = os.path.join(tempfile.gettempdir(), "retrace-link-e2e.camscan.json")
    json.dump(bundle, open(path, "w"))
    other = np.full((600, 800, 3), 120, np.uint8)
    return path, _data_url(other)


def _wait_server(timeout=40):
    for _ in range(timeout * 2):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).status == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


OV = "document.querySelector('.cal-view').__overlay"
TO_SCREEN = (
    "(([ix,iy])=>{const o=" + OV + ";const c=o.canvas;const [cx,cy]=o.vp.imageToCanvas(ix,iy);"
    "const r=c.getBoundingClientRect();"
    "return {x:r.left+cx*r.width/c.width, y:r.top+cy*r.height/c.height};})"
)
POLYS = f"{OV}.ann.items.filter(a=>a.type==='polyline'&&a.closed)"
RECTS = f"{OV}.ann.items.filter(a=>a.type==='rectangle')"


def _tap(pg, pt):
    s = pg.evaluate(TO_SCREEN, list(pt))
    pg.mouse.click(s["x"], s["y"])


def main():
    bundle_path, other_photo = _bundle_file()
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
            pg = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
            # These checks are of the trace as found: no automatic tidy.
            pg.add_init_script("try{localStorage.setItem('camscan.autoTidy','0')}catch(e){}")
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("dialog", lambda d: d.accept())
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && window.CalibPart3D && document.querySelector('.landing-card')",
                                 timeout=15000)
            with pg.expect_file_chooser() as fc:
                pg.click("#cal-landing-load button")
            fc.value.set_files(bundle_path)
            pg.wait_for_selector("#cal-job-panel", timeout=8000)
            pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()
            pg.wait_for_function("(()=>{var v=document.querySelector('.cal-view');return v&&v.__overlay&&v.__overlay.img"
                                 "&&v.__overlay.img.naturalWidth>0&&document.getElementById('cal-kpi');})()", timeout=15000)
            time.sleep(0.6)
            pg.evaluate("document.querySelectorAll('.cal-coach').forEach(n=>n.remove())")

            # 1. Trace, assign Top, nudge the Detail slider, change the scale.
            pg.evaluate(f"(()=>{{const o={OV};o.setMode('outline');o.autoOutline([400,300]);}})()")
            pg.wait_for_function(f"{POLYS}.length===1 && document.getElementById('cal-trace-detail')", timeout=20000)
            pid = pg.evaluate(f"{POLYS}[0].id")
            pg.evaluate(f"(()=>{{const o={OV};o.ann.selectedId={pid};o.setMode('select');o.requestDraw();}})()")
            pg.locator("#cal-ctx-3d").wait_for(state="visible", timeout=5000)
            pg.click("#cal-ctx-3d")
            pg.locator('#cal-part-pick [data-role="top"]').click()
            pg.wait_for_function("window.CalibJob.part().views.top", timeout=5000)
            top0 = pg.evaluate("window.CalibJob.part().views.top")
            pts0 = pg.evaluate(f"JSON.stringify({POLYS}[0].pts)")
            # Assigning closes the Detail bar (it sat over the outline the save message asks the
            # user to check); the same re-trace the slider makes at 'simpler' still follows.
            pg.wait_for_selector("#cal-trace-detail", state="detached", timeout=5000)
            with pg.expect_response(lambda r: "/api/trace" in r.url):
                pg.evaluate(f"{OV}.retraceDetail(0.003)")
            pg.wait_for_function(f"JSON.stringify(({POLYS}[0]||{{}}).pts)!=={json.dumps(pts0)}", timeout=20000)
            after = pg.evaluate(f"{POLYS}.map(a=>a.id)")
            assert after == [top0["ann_id"]], f"re-trace changed the outline's id: {after} vs {top0['ann_id']}"
            w0 = top0["size_mm"][0]
            pg.evaluate(f"(()=>{{const o={OV};o.setManualScaleFromPixels(100, 100);o.requestDraw();}})()")   # 0.5 -> 1.0 mm/px
            pg.wait_for_function(f"Math.abs(window.CalibJob.part().views.top.size_mm[0] - 2*{w0}) < 0.05*{w0}", timeout=8000)
            top1 = pg.evaluate("window.CalibJob.part().views.top")
            assert top1["ann_id"] == top0["ann_id"] and not top1.get("orphan"), f"view cut loose: {top1.get('ann_id')} {top1.get('orphan')}"
            pg.evaluate(f"(()=>{{const o={OV};o.setManualScaleFromPixels(100, 50);}})()")

            # 2. Tap trace, Area box round the part, tap again: one outline, no box.
            pg.evaluate(f"(()=>{{const o={OV};o.clearAll();o.armAutoOutline();}})()")
            with pg.expect_response(lambda r: "/api/trace" in r.url):
                _tap(pg, (400, 300))
            pg.wait_for_function(f"{POLYS}.length===1", timeout=20000)
            pg.evaluate(f"(()=>{{const o={OV};o.setMode('rectangle');}})()")
            _tap(pg, (220, 170))
            time.sleep(0.15)
            _tap(pg, (580, 430))
            pg.wait_for_function(f"{RECTS}.length===1", timeout=5000)
            pg.evaluate(f"{OV}.armAutoOutline()")
            with pg.expect_request(lambda q: "/api/trace" in q.url) as tq:
                _tap(pg, (400, 300))
            assert json.loads(tq.value.post_data).get("roi"), "the Area box was not sent as the trace region"
            pg.wait_for_function(f"{RECTS}.length===0", timeout=20000)
            n = pg.evaluate(f"({{polys:{OV}.ann.items.filter(a=>a.type==='polyline').length, rects:{RECTS}.length}})")
            assert n == {"polys": 1, "rects": 0}, f"tap, box, tap should leave one outline and no box: {n}"
            pg.evaluate(f"{OV}.undo()")
            back = pg.evaluate(f"({{rects:{RECTS}.map(a=>Object.keys(a).filter(k=>/roi/i.test(k))), "
                               f"meas:{OV}.getMeasurements().filter(m=>m.label==='Rect').length}})")
            assert back == {"rects": [[]], "meas": 1}, f"undo did not bring the Area box back as a measurement: {back}"

            # 3. A trace that finds nothing: the Area-box advice, and the next tap traces again.
            far_box = (f"(()=>{{const o={OV};o.clearAll();"
                       f"window.CalibAnn.addRectangle(o.ann,[20,20],[120,90],0.5,'mm',null);o.armAutoOutline();}})()")
            rect_meas = f"{OV}.getMeasurements().filter(m=>m.label==='Rect').length"
            pg.evaluate(far_box)
            pg.route("**/api/trace", lambda route: route.fulfill(status=200, content_type="application/json",
                                                                   body=json.dumps({"error": "no_outline"})))
            with pg.expect_request(lambda q: "/api/trace" in q.url) as tq:
                _tap(pg, (400, 300))
            assert not json.loads(tq.value.post_data).get("roi"), "an Area box the tap is outside was sent as the trace region"
            pg.wait_for_function("document.body.innerText.includes(\"Couldn't separate the part from the background here\")",
                                 timeout=5000)
            pg.unroute("**/api/trace")
            with pg.expect_request(lambda q: "/api/trace" in q.url, timeout=5000):
                _tap(pg, (400, 300))
            assert pg.evaluate(rect_meas) == 1, "a failed trace hid the Area box elsewhere on the photo"
            pg.wait_for_function(f"{POLYS}.length===1", timeout=20000)
            assert pg.evaluate(f"{OV}.selectedPoints.length") == 0, "a hand-trace dot was placed"
            assert pg.evaluate(f"{RECTS}.length") == 1 and pg.evaluate(rect_meas) == 1, \
                "a successful trace removed or hid an Area box it was not inside"

            # 5. Labels at 0.1 mm (copy keeps full precision); no unconfirmed marker drawn.
            rd = pg.evaluate(f"""(()=>{{const o={OV};o.setManualScaleFromPixels(100,50);o._pushHistory();
                window.CalibAnn.addRectangle(o.ann,[10,10],[10+382.8,10+1422.4],0.5,'mm',null);
                return o._readoutFor(o.ann.items[o.ann.items.length-1]);}})()""")
            assert rd["text"].startswith("191.4×711.2 mm"), f"rectangle label: {rd['text']!r}"
            assert rd["copy"] == "191.400x711.200", f"copy lost precision: {rd['copy']!r}"
            drawn = pg.evaluate(f"""(()=>{{const o={OV};const D=window.CalibDraw;const orig=D.drawMarkers;let n=0;
                D.drawMarkers=function(){{n++;return orig.apply(this,arguments);}};
                const keep=o.data;o.data=Object.assign({{}},keep,{{calibration_usable:false,
                  markers:[{{id:1,unverified:true,mm_per_px:0.3,corners:[{{x:20,y:20}},{{x:80,y:20}},{{x:80,y:80}},{{x:20,y:80}}]}}]}});
                o.redraw();o.annotatedDataURL(400);const a=n;
                o._markerConfirmed=true;o.redraw();const b=n-a;o._markerConfirmed=false;
                o.data=keep;D.drawMarkers=orig;o.redraw();return {{unconfirmed:a, confirmed:b}};}})()""")
            assert drawn == {"unconfirmed": 0, "confirmed": 1}, f"unconfirmed marker candidate drawn: {drawn}"

            # 4. A message about this photo goes when the photo does.
            assert pg.evaluate("typeof (window.CalibToast && window.CalibToast.dismiss)") == "function"
            pg.evaluate(f"{OV}._toast('Saved as the Top view (test).', {{autoMs:8000, action:{{label:'🧩 Build 3D part', fn:function(){{}}}}}})")
            assert pg.evaluate("!!(window.__calLastToast && window.__calLastToast.isConnected)")
            pg.evaluate(f"document.querySelector('.cal-view').setAttribute('data-img', {json.dumps(other_photo)})")
            pg.wait_for_function("!(window.__calLastToast && window.__calLastToast.isConnected) && "
                                 "![...document.querySelectorAll('div')].some(d=>d.innerText && d.innerText.startsWith('Saved as the Top view'))",
                                 timeout=5000)

            assert not errors, f"page errors: {errors}"
            browser.close()
        print("E2E_RESULT: PASS (re-trace keeps the Top view's ann_id and the view follows a new scale; "
              "tap, box, tap leaves one outline and undo restores the box; failed trace stays armed; "
              "a box away from the tap is left alone; toast goes with its photo; "
              "0.1 mm labels; unconfirmed marker not drawn)")
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
