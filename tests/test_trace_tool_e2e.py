"""Browser end-to-end test: the rail's ✨ Trace tool traces from one tap, and offers the box.

The rail button used to be "⬡ Outline": it dropped into placing dots by hand, and auto-trace
was a second button that appeared only then. A trace that came back with warnings (the
far end of a cover in shadow, cut short) offered Straighten and Not the part, but not the
box that brings a shadowed end back — that was offered only when nothing traced. Now:
  - the rail button reads "Trace" and one press arms the auto-trace (key 9 too);
  - while armed, "▭ Draw a box" and "✎ Draw by hand" sit in the context row; Draw by hand
    places dots, and "✨ Auto-trace" is offered back;
  - a tap trace with warnings offers "▭ Draw a box" beside Straighten, and it arms the box.

    .venv/bin/python tests/test_trace_tool_e2e.py

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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8215")


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
    path = os.path.join(tempfile.gettempdir(), "trace-tool-e2e-part.jpg")
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


def _shown(pg, sel):
    return pg.evaluate(f"(()=>{{const b=document.querySelector('{sel}');return !!(b&&b.classList.contains('show')&&b.offsetParent);}})()")


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
            replies, sent = [], []

            def on_trace(route):
                sent.append(json.loads(route.request.post_data or "{}"))
                body = replies.pop(0) if replies else {"ok": False, "error": "no_outline", "hint": "nothing queued"}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
            pg.route("**/api/trace", on_trace)
            pg.set_input_files("#uploader input[type=file]", _photo())
            pg.wait_for_function("(()=>{const v=document.querySelector('.cal-view');return v&&v.__overlay&&"
                                 "v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()", timeout=20000)
            # The unscaled photo opens the Set-scale chooser by itself; its backdrop closes it.
            chooser = '.cal-coach-card[aria-label="Set the scale"]'
            pg.wait_for_selector(chooser, state="visible", timeout=8000)
            pg.locator(".cal-coach").click(position={"x": 4, "y": 4})
            pg.wait_for_selector(chooser, state="detached", timeout=5000)
            pg.wait_for_selector(".cal-rail .cal-tool-label", timeout=5000)

            # 1. The rail button reads Trace, and one press arms the auto-trace.
            labels = pg.evaluate("[...document.querySelectorAll('.cal-rail .cal-tool-label')].map(e=>e.textContent)")
            assert "Trace" in labels and "Outline" not in labels, f"rail labels: {labels}"
            pg.locator(".cal-rail .cal-tool", has_text="Trace").click()
            armed = pg.evaluate(f"[{OV}.opts.mode, {OV}._autoOutlineArm]")
            assert armed == ["outline", True], f"Trace did not arm the auto-trace: {armed}"
            assert _shown(pg, "#cal-ctx-box") and _shown(pg, "#cal-ctx-hand"), "box / hand buttons not offered while armed"

            # 2. Draw by hand: dots, not a trace; Auto-trace is offered back.
            pg.click("#cal-ctx-hand")
            assert pg.evaluate(f"{OV}._autoOutlineArm") is False, "Draw by hand left the auto-trace armed"
            assert not _shown(pg, "#cal-ctx-box"), "box button still shown while drawing by hand"
            auto = pg.evaluate("[...document.querySelectorAll('.cal-context .show')].map(b=>b.textContent)")
            assert any("Auto-trace" in t for t in auto), f"Auto-trace not offered back: {auto}"
            pg.evaluate(f"{OV}.setMode('select')")
            pg.keyboard.press("9")
            assert pg.evaluate(f"{OV}._autoOutlineArm") is True, "key 9 did not arm the auto-trace"

            # 3. A tap trace with warnings offers the box beside Straighten; it arms the box.
            replies.append({"ok": True, "points": SLAB, "holes": [],
                            "warnings": ["The left side steps out 17 px near the top end — shadow?"]})
            c = pg.evaluate(f"""(()=>{{const o={OV}, r=o.canvas.getBoundingClientRect(), q=o.vp.imageToCanvas(400,300);
                return [r.left+q[0]*r.width/o.canvas.width, r.top+q[1]*r.height/o.canvas.height];}})()""")
            pg.mouse.click(c[0], c[1])
            box = pg.locator(".cal-toast-action", has_text="Draw a box")
            box.wait_for(state="visible", timeout=8000)
            acts = pg.evaluate("[...window.__calLastToast.querySelectorAll('.cal-toast-action')].map(b=>b.textContent)")
            assert any("Straighten" in a for a in acts), f"toast actions: {acts}"
            box.click()
            assert pg.evaluate(f"[{OV}.opts.mode, {OV}._traceAfterBox]") == ["rectangle", True], "Draw a box did not arm"
            assert len(sent) == 1, f"{len(sent)} trace requests"

            assert not errors, f"page errors: {errors}"
            browser.close()
        print("E2E_RESULT: PASS (rail Trace arms the auto-trace in one press, key 9 too; Draw a box and Draw by hand "
              "offered while armed; a trace with warnings offers Draw a box beside Straighten)")
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
