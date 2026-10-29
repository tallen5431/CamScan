"""Browser end-to-end test: adding and removing an outline's dots, and Snap to edge.

There was no way to add a dot to an outline (to follow a curve the trace cut across) or to
remove one, and no tool that follows an edge Refine cannot fit. Now:
  - the selected outline shows a "+" between its dots: a press adds a dot there and drags
    it, in one undo step; a press on an outline not yet selected only selects it;
  - a double-click (double-tap) on a dot removes it, one undo step; an outline keeps 3;
  - Tidy ▾ has "🧲 Snap to edge": it posts mode "snap" and applies the outline it gets back.

    .venv/bin/python tests/test_dot_edit_e2e.py

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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8217")


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
    path = os.path.join(tempfile.gettempdir(), "dot-edit-e2e-part.jpg")
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


def _scr(pg, x, y):
    return pg.evaluate(f"""(([x,y])=>{{const o={OV}, r=o.canvas.getBoundingClientRect(), q=o.vp.imageToCanvas(x,y);
        return [r.left+q[0]*r.width/o.canvas.width, r.top+q[1]*r.height/o.canvas.height];}})""", [x, y])


def _pts(pg):
    return pg.evaluate(f"{OV}.ann.items.find(a=>a.type==='polyline').pts.map(p=>[Math.round(p[0]),Math.round(p[1])])")


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
            pg.evaluate("localStorage.setItem('calib.coachSeen','1');localStorage.setItem('camscan.simplemode','0')")
            pg.reload(wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
            pg.set_input_files("#uploader input[type=file]", _photo())
            pg.wait_for_function("(()=>{const v=document.querySelector('.cal-view');return v&&v.__overlay&&"
                                 "v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()", timeout=20000)
            chooser = '.cal-coach-card[aria-label="Set the scale"]'
            pg.wait_for_selector(chooser, state="visible", timeout=8000)
            pg.locator(".cal-coach").click(position={"x": 4, "y": 4})
            pg.wait_for_selector(chooser, state="detached", timeout=5000)
            pg.evaluate(f"""(()=>{{const o={OV};o.setMode('select');
                window.CalibAnn.addPolyline(o.ann,{json.dumps(SLAB)},0.5,'mm',null,true);o.ann.selectedId=null;o.requestDraw();}})()""")

            # 1. Not selected yet: a press on a side's middle only selects it.
            x, y = _scr(pg, 400, 230)
            pg.mouse.click(x, y)
            assert len(_pts(pg)) == 4, f"a first press added a dot: {_pts(pg)}"
            assert pg.evaluate(f"{OV}.ann.selectedId!=null"), "the outline was not selected"

            # 2. Selected: press the "+" and drag — a new dot, where it was dropped; one undo.
            undo0 = pg.evaluate(f"{OV}._undoStack.length")
            pg.mouse.move(x, y)
            pg.mouse.down()
            for k in range(1, 6):
                pg.mouse.move(x, y - 4 * k)
            pg.mouse.up()
            pts = _pts(pg)
            assert len(pts) == 5 and pts[1][0] == 400 and pts[1][1] < 225, f"+ did not add and drag a dot: {pts}"
            assert pg.evaluate(f"{OV}._undoStack.length") == undo0 + 1, "adding a dot was not one undo step"
            pg.evaluate(f"{OV}.undo()")
            assert len(_pts(pg)) == 4, f"undo kept the added dot: {_pts(pg)}"
            pg.evaluate(f"{OV}.redo()")
            assert len(_pts(pg)) == 5, "redo lost the added dot"

            # 3. Double-click a dot: removed; an outline keeps three.
            dx, dy = _scr(pg, *_pts(pg)[1])
            pg.mouse.dblclick(dx, dy)
            assert _pts(pg) == SLAB, f"double-click did not remove the dot: {_pts(pg)}"
            for _ in range(3):
                cx, cy = _scr(pg, *_pts(pg)[0])
                pg.mouse.dblclick(cx, cy)
            assert len(_pts(pg)) == 3, f"an outline went below three dots: {_pts(pg)}"
            pg.evaluate(f"{OV}.undo()")
            assert len(_pts(pg)) == 4, "undo did not bring the removed dot back"

            # 4. Tidy ▾ → Snap to edge: posts mode "snap" and applies the answer.
            sent = []
            back = [[220, 230], [400, 226], [580, 230], [580, 370], [220, 370]]

            def on_snap(route):
                sent.append(json.loads(route.request.post_data or "{}"))
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "points": back, "dots": 5, "found_pct": 80, "moved_px": 1.0}))
            pg.route("**/api/outline/straighten", on_snap)
            # (Tidy ▾ itself shows once the photo has a scale; its menu holds the button.)
            assert pg.evaluate("!!document.querySelector('#cal-tidy-menu #cal-ctx-snap')"), "no Snap to edge in Tidy"
            pg.evaluate(f"(()=>{{const o={OV};o.ann.selectedId=o.ann.items.find(a=>a.type==='polyline').id;}})()")   # undo deselects
            pg.evaluate("document.getElementById('cal-ctx-snap').click()")
            pg.wait_for_function(f"{OV}.ann.items.find(a=>a.type==='polyline').pts.length===5", timeout=8000)
            assert sent and sent[0].get("mode") == "snap", f"Snap to edge sent {sent[:1]}"
            shown = pg.evaluate("(()=>{const t=window.__calLastToast;return t?t.innerText:'';})()")
            assert "Snapped" in shown and "80%" in shown, f"snap message: {shown!r}"

            assert not errors, f"page errors: {errors}"
            browser.close()
        print("E2E_RESULT: PASS (+ adds a dot on the selected outline and drags it, one undo; a first press only "
              "selects; double-click removes a dot, never below 3; Tidy → Snap to edge posts mode snap and applies it)")
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
