"""Browser end-to-end test: Trace in one gesture, the ruler counted, tidied straight away, weak spots marked.

  - the armed ✨ Trace takes a drag: a box round the part, traced inside it (one gesture,
    where it used to be Draw a box, then a tap);
  - the trace carries the scale line (`ruler`), so the server can add a ruler lying on the
    part back into the outline, and the message says it did;
  - a fresh trace is tidied straight away (Refine): one Undo gives the trace as found;
  - stretches the server finds with no clear edge come back as `weak`, are kept on the
    outline and drawn in amber, and the message offers ▭ Fix one area;
  - Settings → "Tidy a trace straight away" off: the trace is left as found.
The server is stubbed, so the page's side is checked exactly.

    .venv/bin/python tests/test_trace_flow_e2e.py

It SKIPS cleanly (exit 0) where Playwright or a browser isn't available — unless
CAMSCAN_E2E_REQUIRE=1, as CI sets it.
"""
import os
import sys
import json
import time
import tempfile
import subprocess
import urllib.request
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PORT = os.getenv("CAMSCAN_E2E_PORT", "8219")


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

OV = "document.querySelector('.cal-view').__overlay"
SLAB = [[220, 230], [580, 230], [580, 370], [220, 370]]


def _photo():
    img = np.full((600, 800, 3), 195, np.uint8)
    cv2.rectangle(img, (220, 230), (580, 370), (55, 55, 55), -1)
    path = os.path.join(tempfile.gettempdir(), "trace-flow-e2e-part.jpg")
    cv2.imwrite(path, img)
    return path


def _wait_server(timeout=40):
    for _ in range(timeout * 2):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).status == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


TRACE = [[221, 231], [579, 229], [581, 371], [219, 369], [220, 300]]
REFINED = [[220, 230], [580, 230], [580, 370], [220, 370]]


def _scr(pg, x, y):
    return pg.evaluate(f"""(([x,y])=>{{const o={OV}, r=o.canvas.getBoundingClientRect(), q=o.vp.imageToCanvas(x,y);
        return [r.left+q[0]*r.width/o.canvas.width, r.top+q[1]*r.height/o.canvas.height];}})""", [x, y])


def _drag_box(pg, a, b):
    """Drag from photo point a to b; returns both ends as the page's own gestures map them."""
    (x0, y0), (x1, y1) = _scr(pg, *a), _scr(pg, *b)
    ends = pg.evaluate(f"""(p)=>p.map(([x,y])=>{OV}.vp.screenToImage({{clientX:x, clientY:y}}))""", [[x0, y0], [x1, y1]])
    pg.mouse.move(x0, y0)
    pg.mouse.down()
    for k in range(1, 9):
        pg.mouse.move(x0 + (x1 - x0) * k / 8, y0 + (y1 - y0) * k / 8)
    pg.mouse.up()
    return ends


def _toast(pg):
    return pg.evaluate("(()=>{const t=window.__calLastToast;return t&&t.parentNode?t.innerText:'';})()")


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
            pg = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
            pg.evaluate("localStorage.setItem('calib.coachSeen','1');localStorage.setItem('camscan.simplemode','0');"
                        "localStorage.removeItem('camscan.autoTidy')")
            pg.reload(wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
            traces, fixes = [], []

            def on_trace(route):
                traces.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "points": TRACE, "holes": [], "ruler_filled": True}))

            def on_fix(route):
                fixes.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "points": REFINED, "straight": 4, "snapped": 4, "rounded": 0,
                                               "weak": [[[300, 230], [350, 230], [400, 230]]]}))
            pg.route("**/api/trace", on_trace)
            pg.route("**/api/outline/straighten", on_fix)
            pg.set_input_files("#uploader input[type=file]", _photo())
            pg.wait_for_function("(()=>{const v=document.querySelector('.cal-view');return v&&v.__overlay&&"
                                 "v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()", timeout=20000)
            chooser = '.cal-coach-card[aria-label="Set the scale"]'
            pg.wait_for_selector(chooser, state="visible", timeout=8000)
            pg.locator(".cal-coach").click(position={"x": 4, "y": 4})
            pg.wait_for_selector(chooser, state="detached", timeout=5000)
            pg.evaluate(f"{OV}.setManualScaleFromPixels(280, 300, [[230,300],[510,300]])")

            # 1. Trace: one drag round the part traces inside it, with the scale line sent.
            pg.locator(".cal-rail .cal-tool", has_text="Trace").click()
            (ax, ay), (bx, by) = _drag_box(pg, (200, 210), (600, 390))
            pg.wait_for_function("(()=>{const t=window.__calLastToast;return t&&t.innerText.includes('Traced and tidied');})()",
                                 timeout=10000)
            roi = traces[0].get("roi")
            # The box round the part (220..580 x 230..370), sent as the roi. (Exact numbers vary by
            # a few % with the runner's layout — the canvas's cached size — so this checks what
            # matters: a box from the drag, holding the part, nothing like the whole photo.)
            assert roi and roi[0] <= 220 and roi[0] + roi[2] >= 580 and roi[1] <= 230 and roi[1] + roi[3] >= 370 \
                and roi[2] < 500 and roi[3] < 260, f"box not sent as the roi: {roi} (drawn ~{[ax, ay, bx, by]})"
            assert traces[0].get("ruler") == [[230, 300], [510, 300]], f"scale line not sent: {traces[0].get('ruler')}"
            assert not pg.evaluate(f"{OV}.ann.items.some(a=>a.type==='rectangle')"), "the box was left as an Area"

            # 2. Tidied straight away; the ruler and the weak stretch are said; one Undo = as found.
            assert fixes and fixes[-1].get("mode") == "refine", f"no tidy after the trace: {fixes}"
            pts = pg.evaluate(f"{OV}.ann.items.find(a=>a.type==='polyline').pts")
            assert [[round(x), round(y)] for x, y in pts] == REFINED, f"outline not the tidied one: {pts}"
            shown = _toast(pg)
            assert "ruler lying on it" in shown and "Amber" in shown and "Undo for the trace as found" in shown, f"message: {shown!r}"
            assert pg.locator(".cal-toast-action", has_text="Fix one area").count() >= 1, "no Fix one area offered"
            assert pg.evaluate(f"{OV}._weakCount({OV}.ann.items.find(a=>a.type==='polyline'))") == 1, "weak stretch not kept"
            pg.evaluate(f"{OV}.undo()")
            pts = pg.evaluate(f"{OV}.ann.items.find(a=>a.type==='polyline').pts")
            assert [[round(x), round(y)] for x, y in pts] == TRACE, f"Undo did not give the trace as found: {pts}"

            # 3. Settings off: a trace is left as found.
            pg.evaluate(f"(()=>{{localStorage.setItem('camscan.autoTidy','0');const o={OV};o.clearAll();}})()")
            n_fix = len(fixes)
            pg.locator(".cal-rail .cal-tool", has_text="Trace").click()
            _drag_box(pg, (200, 210), (600, 390))
            pg.wait_for_function("(()=>{const t=window.__calLastToast;return t&&t.innerText.includes('traced');})()", timeout=10000)
            pg.wait_for_timeout(600)
            assert len(fixes) == n_fix, "tidied with the setting off"
            assert "Traced and tidied" not in _toast(pg), f"message with tidy off: {_toast(pg)!r}"

            assert not errors, f"page errors: {errors}"
            browser.close()
        print("E2E_RESULT: PASS (a box dragged in Trace traces inside it, the scale line sent; tidied straight away, "
              "ruler and amber stretch said, Fix one area offered, Undo gives the trace as found; setting off leaves it)")
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
