"""Browser end-to-end for setting the scale on a phone, and the phone layout round it.

A phone in Chromium (390x844, touch, mobile), driving the canvas with real touch taps:

  1. Ghost tap: the second Set-scale tap places point 2 on pointerup and opens the length
     box at once; the browser's click from that SAME tap then landed on whatever sat under
     the finger in the new box — on a customer photo, the "Letter" preset, which set a
     279.4 mm scale on a 127 mm ruler with no box ever shown. The line here runs down from
     near the top of the photo, so there is no room above it and the box drops below, over
     the second point, exactly as it did. After the tap the box must be up, the scale unset.
  2. The ruler chips: "30 cm" sets 300 mm over the line, and the box says to start and end
     on numbered marks. The tool hint talks about the ruler and says "tap".
  3. The status strip grows once a scale is set; a view still at fit is refit, so the
     photo's bottom edge stays inside the canvas.
  4. After a trace on the phone, the Detail bar fits the screen (its Done is on it) and
     does not cover the outline's buttons.
  5. No pixels passed off as mm: with the scale cleared, partProfile() is null.
  6. On a desktop page, a mouse drag in Area mode draws one rectangle.

Not part of the fast smoke test (needs Chromium + a running server). Run directly:
    .venv/bin/python tests/test_scale_touch_e2e.py
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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8196")


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


def _bundle_file():
    # A dark board on a light floor, with no scale: the phone has to set one.
    img = np.full((900, 600, 3), 188, np.uint8)
    cv2.rectangle(img, (170, 260), (430, 760), (60, 62, 66), -1)
    _ok, enc = cv2.imencode(".jpg", img)
    raw = "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode()
    bundle = {
        "kind": "camscan.job", "version": 1, "id": "scale-touch-e2e", "createdAt": "2026-01-01T00:00:00Z",
        "brief": {"part": "board"},
        "views": [{
            "label": "Top", "image": raw, "units": "mm",
            "scale": {"calibrated": False, "source": "none"}, "measurements": [],
            "restore": {"v": 1, "w": 600, "h": 900, "raw": raw,
                        "calib": {"markers": [], "image_size": {"width": 600, "height": 900}, "marker_size_mm": None},
                        "ann": [], "units": "mm", "manual": {"mmPerPx": None, "homography": None, "ref": None}},
        }],
    }
    path = os.path.join(tempfile.gettempdir(), "scale-touch-e2e.camscan.json")
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


OV = "document.querySelector('.cal-view').__overlay"
# Screen position of an image point, through the overlay's own transform.
TO_SCREEN = (
    "(([ix,iy])=>{const o=" + OV + ";const c=o.canvas;const [cx,cy]=o.vp.imageToCanvas(ix,iy);"
    "const r=c.getBoundingClientRect();"
    "return {x:r.left+cx*r.width/c.width, y:r.top+cy*r.height/c.height};})"
)
# Image point under a screen position (the inverse of TO_SCREEN).
TO_IMAGE = (
    "(([x,y])=>{const o=" + OV + ";const c=o.canvas;const r=c.getBoundingClientRect();"
    "return o.vp.canvasToImage((x-r.left)*c.width/r.width,(y-r.top)*c.height/r.height);})"
)


def _open(pg, bundle_path):
    pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
    with pg.expect_file_chooser() as fc:
        pg.click("#cal-landing-load button")
    fc.value.set_files(bundle_path)
    pg.wait_for_selector("#cal-job-panel", timeout=8000)
    pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()
    pg.wait_for_function(
        "(()=>{var v=document.querySelector('.cal-view');return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0"
        "&&document.getElementById('cal-kpi');})()", timeout=15000)
    # A photo with no scale opens the Set-the-scale chooser by itself (after 450 ms). This
    # drives the overlay directly, so close it, and any first-run card, the way a person would.
    time.sleep(0.9)
    for _ in range(3):
        close = pg.locator(".cal-coach button", has_text="Close")
        if close.count() and close.first.is_visible():
            close.first.click()
            time.sleep(0.2)
    pg.evaluate("document.querySelectorAll('.cal-coach').forEach(n=>n.remove())")


def _launch(p):
    launch = {"args": ["--no-proxy-server", "--no-sandbox"]}
    if _CHROMIUM:
        launch["executable_path"] = _CHROMIUM
    return p.chromium.launch(**launch)


def phone(browser, bundle_path):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                              has_touch=True, is_mobile=True)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    _open(pg, bundle_path)
    pg.evaluate(f"(()=>{{const o={OV};o.clearManualScale();o.setMode('setscale');o.requestDraw();}})()")
    hint = pg.evaluate(f"{OV}._toolHint()")
    assert "tap one end of the ruler" in hint, f"set-scale hint does not talk about the ruler: {hint!r}"

    # Every click the document sees, so a preset pressed by the ghost click shows up by name.
    pg.evaluate("window.__clicks=[];document.addEventListener('click',e=>window.__clicks.push("
                "(e.target.textContent||e.target.tagName).slice(0,40)),true)")
    # The line runs down the board from close to the photo's top edge: no room above its
    # middle for the box, so the box goes below it — where point 2 is.
    ctop = pg.evaluate(f"{OV}.canvas.getBoundingClientRect().top")
    a_img = pg.evaluate(TO_IMAGE, [195, ctop + 14])
    b_img = pg.evaluate(TO_IMAGE, [195, ctop + 150])
    a = pg.evaluate(TO_SCREEN, a_img)
    b = pg.evaluate(TO_SCREEN, b_img)
    pg.touchscreen.tap(a["x"], a["y"])
    time.sleep(0.25)
    pg.touchscreen.tap(b["x"], b["y"])
    time.sleep(0.6)
    st = pg.evaluate(f"""(()=>{{const o={OV};const box=document.getElementById('cal-scale-input');
        const r=box&&box.getBoundingClientRect();
        return {{box:!!box&&r.width>0, rect:r?[r.left,r.top,r.right,r.bottom]:null, scale:o.getScale(),
                 manual:o.opts.manualMmPerPx, clicks:window.__clicks}};}})()""")
    assert st["box"], f"the length box never stayed up after the 2nd tap (clicks {st['clicks']})"
    assert not st["manual"] and not st["scale"], f"a scale was set by the tap itself: {st}"
    l, t, r, btm = st["rect"]
    for q in (a, b):
        assert not (l <= q["x"] <= r and t <= q["y"] <= btm), f"length box covers a scale point {q} {st['rect']}"
    txt = pg.inner_text("#cal-scale-input")
    assert "numbered marks" in txt, f"no ruler-marks hint in the length box: {txt!r}"
    hint2 = pg.evaluate(f"{OV}._toolHint()")
    assert "type the length between your two marks" in hint2, f"hint after both taps: {hint2!r}"

    # 2. The 30 cm chip: 300 mm over the line just drawn.
    line_px = ((a_img[0] - b_img[0]) ** 2 + (a_img[1] - b_img[1]) ** 2) ** 0.5
    pg.locator("#cal-scale-input button", has_text="30 cm").tap()
    pg.wait_for_function(f"{OV}.opts.manualMmPerPx>0", timeout=5000)
    got = pg.evaluate(f"{OV}.opts.manualMmPerPx")
    assert abs(got - 300.0 / line_px) / (300.0 / line_px) < 0.03, f"30 cm chip: {got} vs {300.0 / line_px}"

    # 3. The strip grew with the scale's chips; the view had not been zoomed, so the photo
    #    is refit and its bottom edge stays inside the canvas.
    time.sleep(0.8)
    fit = pg.evaluate(f"""(()=>{{const o={OV};const c=o.canvas;const H=o.img.naturalHeight;
        const y=o.vp.imageToCanvas(0,H)[1];return {{y:y, ch:c.height, atFit:Math.abs(o.vp.k-o._fitK)<1e-4}};}})()""")
    assert fit["atFit"] and fit["y"] <= fit["ch"] + 1.5, f"photo runs past the canvas bottom after the scale: {fit}"

    # 4. A trace on the phone: the Detail bar stays on screen and clear of the outline's buttons.
    pg.evaluate(f"(()=>{{const o={OV};o.setMode('outline');o.autoOutline([300,500]);}})()")
    pg.wait_for_function("document.getElementById('cal-trace-detail') && "
                         f"{OV}.ann.items.some(a=>a.type==='polyline'&&a.closed)", timeout=20000)
    time.sleep(1.2)   # the outline's buttons appear a moment after the trace selects it
    lay = pg.evaluate("""(()=>{const done=[...document.querySelectorAll('#cal-trace-detail button')].find(b=>b.textContent.includes('Done'));
        const d=done.getBoundingClientRect();
        const btns=[...document.querySelectorAll('.cal-context button.show')].filter(b=>b.getBoundingClientRect().height>0);
        const blocked=btns.filter(b=>{const r=b.getBoundingClientRect();const e=document.elementFromPoint((r.left+r.right)/2,(r.top+r.bottom)/2);
            return !(e===b||b.contains(e));}).map(b=>b.textContent.trim());
        return {doneRight:d.right, doneLeft:d.left, w:innerWidth, n:btns.length, blocked:blocked};})()""")
    assert lay["doneRight"] <= lay["w"] and lay["doneLeft"] >= 0, f"Detail bar's Done is off the screen: {lay}"
    assert lay["n"] > 0 and not lay["blocked"], f"outline buttons covered: {lay}"

    # 5. Clear the scale: the outline is still there and selected, but it has no millimetres.
    prof = pg.evaluate(f"(()=>{{const o={OV};const p=o.ann.items.find(a=>a.type==='polyline'&&a.closed);"
                       "o.ann.selectedId=p.id;const before=!!o.partProfile();o.clearManualScale();"
                       "return {before:before, after:o.partProfile(), byId:o.partProfile(p.id)};})()")
    assert prof["before"] and prof["after"] is None and prof["byId"] is None, f"partProfile without a scale: {prof}"
    assert not errors, f"page errors: {errors}"
    ctx.close()


def desktop(browser, bundle_path):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    _open(pg, bundle_path)
    pg.evaluate(f"(()=>{{const o={OV};o.setManualScaleFromPixels(100,50);o.setMode('rectangle');o.requestDraw();}})()")
    assert "drag, or tap two corners" in pg.evaluate(f"{OV}._toolHint()"), "Area hint does not mention the drag"
    a = pg.evaluate(TO_SCREEN, [200, 300])
    b = pg.evaluate(TO_SCREEN, [400, 600])
    pg.mouse.move(a["x"], a["y"])
    pg.mouse.down()
    for i in range(1, 9):
        pg.mouse.move(a["x"] + (b["x"] - a["x"]) * i / 8, a["y"] + (b["y"] - a["y"]) * i / 8)
    pg.mouse.up()
    time.sleep(0.3)
    rects = pg.evaluate(f"{OV}.ann.items.filter(a=>a.type==='rectangle').map(a=>a.rect)")
    assert len(rects) == 1, f"a drag in Area mode should draw one rectangle: {rects}"
    x1, y1, x2, y2 = rects[0]
    assert abs(x1 - 200) < 8 and abs(y1 - 300) < 8 and abs(x2 - 400) < 8 and abs(y2 - 600) < 8, f"rectangle off: {rects}"
    # Two taps still work.
    for q in ((100, 100), (150, 180)):
        s = pg.evaluate(TO_SCREEN, list(q))
        pg.mouse.click(s["x"], s["y"])
        time.sleep(0.15)
    n = pg.evaluate(f"{OV}.ann.items.filter(a=>a.type==='rectangle').length")
    assert n == 2, f"two taps no longer draw an Area box: {n}"
    assert not errors, f"page errors: {errors}"
    ctx.close()


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
                browser = _launch(p)
            except Exception as e:
                _skip(f"cannot launch chromium: {e}")
            phone(browser, bundle_path)
            desktop(browser, bundle_path)
            browser.close()
        print("E2E_RESULT: PASS (ghost tap swallowed, box clear of both points; 30 cm chip; refit after the "
              "strip grew; Detail bar on screen and clear of the buttons; no profile without a scale; Area drag)")
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
