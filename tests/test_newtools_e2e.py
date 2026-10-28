"""Browser end-to-end test for the tools added for the customer step-pad photos.

Drives each one through the REAL interface in Chromium — buttons clicked, the canvas tapped at
real screen positions — because each of these is a few lines of glue between a UI control and
logic that the smoke test already checks in isolation, and glue is where they break:

  1. "✖ Not the part": trace a dark part beside its same-coloured shadow (the trace swallows
     the shadow), press the button, tap the shadow ON THE CANVAS, and the re-trace must shrink
     to the part.
  2. Runaway guard: tapping a featureless photo must say why, not add an outline.
  3. Job panel: two views that disagree about a dimension show the warning with both readings;
     the capture guide opens.
  4. Hollow cover: the Depth dialog's wall field reaches the profile, and the STL comes back
     as a shell.

Not part of the fast smoke test (it needs Chromium + a running server). Run it directly:

    .venv/bin/python tests/test_newtools_e2e.py

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

PART = [[150, 120], [470, 120], [470, 300], [150, 300]]
PART_AREA = 320.0 * 180.0


def _data_url(img):
    _ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode()


def _view(label, img, measurements=()):
    h, w = img.shape[:2]
    raw = _data_url(img)
    return {
        "label": label, "image": raw, "units": "mm",
        "scale": {"calibrated": True, "source": "manual line"},
        "measurements": [dict(m) for m in measurements],
        "restore": {"v": 1, "w": w, "h": h, "raw": raw,
                    "calib": {"markers": [], "image_size": {"width": w, "height": h},
                              "marker_size_mm": None},
                    "ann": [], "units": "mm",
                    "manual": {"mmPerPx": 0.5, "homography": None, "ref": None}},
    }


def _bundle(name, views):
    path = os.path.join(tempfile.gettempdir(), f"newtools-{name}.camscan.json")
    json.dump({"kind": "camscan.job", "version": 1, "id": f"newtools-{name}",
               "createdAt": "2026-01-01T00:00:00Z", "brief": {"part": name}, "views": views},
              open(path, "w"))
    return path


def _shadow_scene():
    # The measured case: part 71 grey, its own shadow 57, ground 169 — part and shadow are
    # 15 levels apart, so no threshold separates them.
    img = np.full((520, 760, 3), 170, np.uint8)
    cv2.fillPoly(img, [np.array([[180, 300], [500, 300], [530, 430], [210, 430]], np.int32)], (58, 58, 58))
    cv2.fillPoly(img, [np.array(PART, np.int32)], (72, 72, 72))
    return cv2.GaussianBlur(img, (5, 5), 0)


def _flat_scene():
    rng = np.random.default_rng(3)
    img = np.full((500, 700, 3), 128, np.int16) + rng.integers(-3, 4, (500, 700, 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def _wait_server(timeout=40):
    for _ in range(timeout * 2):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).status == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


OV = "document.querySelector('.cal-view').__overlay"
OUTERS = f"(()=>{OV}.ann.items.filter(a=>a.type==='polyline'&&a.closed))()"
# Screen position of an image point, through the overlay's own transform — so the test taps
# the canvas exactly where a person would.
TO_SCREEN = (
    "(([ix,iy])=>{const o=" + OV + ";const c=o.canvas;const [cx,cy]=o.vp.imageToCanvas(ix,iy);"
    "const r=c.getBoundingClientRect();const cs=getComputedStyle(c);"
    "const bl=parseFloat(cs.borderLeftWidth)||0,bt=parseFloat(cs.borderTopWidth)||0;"
    "const pl=parseFloat(cs.paddingLeft)||0,pt=parseFloat(cs.paddingTop)||0;"
    "const cw=o.vp._cw||r.width,ch=o.vp._ch||r.height;"
    "const x=r.left+bl+pl+cx*cw/c.width,y=r.top+bt+pt+cy*ch/c.height;"
    "const top=document.elementFromPoint(x,y);return {x,y,onCanvas:top===c};})"
)


def _area(pts):
    a = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def _open(browser, bundle_path, errors, dialogs):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
    with pg.expect_file_chooser() as fc:
        pg.click("#cal-landing-load button")
    fc.value.set_files(bundle_path)
    pg.wait_for_selector("#cal-job-panel", timeout=8000)
    return ctx, pg


def _open_side(pg):
    pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()
    pg.wait_for_function(
        "(()=>{var v=document.querySelector('.cal-view');return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()",
        timeout=15000)


def scenario_not_the_part(browser, errors, dialogs):
    ctx, pg = _open(browser, _bundle("shadow", [_view("Top", _shadow_scene())]), errors, dialogs)
    _open_side(pg)
    pg.evaluate(f"(()=>{{const o={OV};o.setMode('outline');o.autoOutline([310,210]);}})()")
    pg.wait_for_function(f"{OUTERS}.length===1", timeout=20000)
    first = pg.evaluate(OUTERS)[0]
    plain = _area(first["pts"]) / PART_AREA
    assert plain > 1.4, f"precondition: the plain trace should swallow the shadow, got {plain:.2f}x"

    # The real button, then a real tap on the shadow.
    pg.evaluate(f"window.__firstPts=JSON.stringify({OUTERS}[0].pts)")
    btn = pg.locator("button", has_text="Not the part")
    assert btn.is_visible(), "'Not the part' is not shown after a trace"
    btn.click()
    where = pg.evaluate(TO_SCREEN, [350, 390])
    assert where["onCanvas"], f"the shadow is covered by UI at {where}"
    with pg.expect_request(lambda r: "/api/trace" in r.url, timeout=20000) as req:
        pg.mouse.click(where["x"], where["y"])
    body = json.loads(req.value.post_data or "{}")
    assert len(body.get("bg") or []) == 1, f"the tap did not reach the trace as a background hint: {body.get('bg')}"
    # The re-trace replaces the outline in place: same id (a 3D view follows it), new points.
    pg.wait_for_function(f"(()=>{{const p={OUTERS};return p.length===1&&p[0].id==={first['id']}"
                         f"&&JSON.stringify(p[0].pts)!==window.__firstPts;}})()",
                         timeout=20000)
    hinted = _area(pg.evaluate(OUTERS)[0]["pts"]) / PART_AREA
    assert 0.75 <= hinted <= 1.35, f"after the tap the trace should be the part, got {hinted:.2f}x"
    return ctx, pg, plain, hinted


def scenario_wall(pg):
    # The outline from the previous scenario is selected; use the Depth dialog for real.
    pg.evaluate(f"(()=>{{const o={OV};const p=o.ann.items.find(a=>a.type==='polyline'&&a.closed);"
                f"o.ann.selectedId=p.id;o.setMode('select');o.requestDraw();}})()")
    depth_btn = pg.locator("button", has_text="Extrude")
    depth_btn.first.click()
    pg.wait_for_selector("#cal-depth-input", timeout=5000)
    pg.locator("#cal-depth-input input[type=number]").first.fill("25")
    pg.fill("#cal-wall-input", "3")
    pg.locator("#cal-depth-input").get_by_role("button", name="Set").click()
    prof = pg.evaluate(f"(()=>{{const p={OV}.ann.items.find(a=>a.type==='polyline'&&a.closed);"
                       "return {d:p.depth_mm,w:p.wall_mm};})()")
    assert prof == {"d": 25, "w": 3}, f"Depth dialog did not set depth and wall: {prof}"
    label = pg.locator("button", has_text="3 wall")
    assert label.count() >= 1, "the Depth button does not show the wall"

    with pg.expect_response(lambda r: "/api/export/stl" in r.url, timeout=30000) as resp:
        pg.evaluate(f"{OV}.saveSTL()")
    r = resp.value
    assert r.status == 200, f"STL export failed: {r.status} {r.text()[:200]}"
    kind = r.headers.get("x-camscan-kind")
    assert kind == "shell", f"expected a hollow cover, server built: {kind}"
    pg.wait_for_function("document.body.innerText.includes('plate-down')", timeout=8000)
    return kind


def scenario_runaway(browser, errors, dialogs):
    ctx, pg = _open(browser, _bundle("flat", [_view("Top", _flat_scene())]), errors, dialogs)
    _open_side(pg)
    with pg.expect_response(lambda r: "/api/trace" in r.url, timeout=20000) as resp:
        pg.evaluate(f"(()=>{{const o={OV};o.setMode('outline');o.autoOutline([350,250]);}})()")
    j = resp.value.json()
    assert j.get("ok") is False and j.get("error") == "runaway", f"server did not refuse: {j}"
    pg.wait_for_function("document.body.innerText.includes('covers the whole photo')", timeout=8000)
    n = len(pg.evaluate(OUTERS))
    assert n == 0, f"a refused trace still added {n} outline(s)"
    ctx.close()


def scenario_job_panel(browser, errors, dialogs):
    img = _shadow_scene()
    views = [_view("Top", img, [{"label": "Board", "text": "396 mm", "mm": 396.0}]),
             _view("Detail", img, [{"label": "Board", "text": "60 mm", "mm": 60.0}])]
    ctx, pg = _open(browser, _bundle("disagree", views), errors, dialogs)
    panel = pg.locator("#cal-job-panel")
    text = panel.inner_text()
    assert "Scales disagree" in text, "no disagreement warning in the job panel"
    assert "396.0 mm" in text and "60.0 mm" in text, "the warning does not name both readings"
    guide = panel.get_by_role("button", name="What makes a photo measurable")
    guide.click()
    assert "Put the reference ON the face" in panel.inner_text(), "capture guide did not open"
    ctx.close()


def main():
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
            errors, dialogs = [], []
            ctx, pg, plain, hinted = scenario_not_the_part(browser, errors, dialogs)
            kind = scenario_wall(pg)
            ctx.close()
            scenario_runaway(browser, errors, dialogs)
            scenario_job_panel(browser, errors, dialogs)
            assert not errors, f"page errors: {errors}"
            assert not dialogs, f"unexpected alerts: {dialogs}"
            browser.close()
        print(f"E2E_RESULT: PASS (not-the-part tap {plain:.2f}x -> {hinted:.2f}x the part; "
              f"cover STL kind={kind}; runaway refused; disagreement + guide shown)")
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
