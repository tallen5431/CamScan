"""Browser end-to-end test: Quick measure — photograph a part, read its sizes.

  - the landing card has "📏 Quick measure" (the photo it picks opens in Quick measure) and
    the wallet marker card's link, which downloads;
  - with simple mode on (the default), Quick measure has the screen: its panel, no card;
  - unscaled, the panel says so; once scaled, the Measure tool is in hand and the panel names
    the scale's basis (a ruler line: square-on only);
  - two taps add a length, listed as A with its ± ("180.0 ± 2.0 mm"), and the canvas label
    carries the same letter; a second is B; Copy all copies both; ✕ deletes one;
  - Settings' ± off drops the ±; ✓ Done goes back to the full tool.

    .venv/bin/python tests/test_quick_measure_e2e.py

It SKIPS cleanly (exit 0) where Playwright or a browser isn't available — unless
CAMSCAN_E2E_REQUIRE=1, as CI sets it.
"""
import os
import re
import sys
import time
import tempfile
import subprocess
import urllib.request
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PORT = os.getenv("CAMSCAN_E2E_PORT", "8221")


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
    path = os.path.join(tempfile.gettempdir(), "quick-e2e-part.jpg")
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


def _tap(pg, x, y):
    sx, sy = _scr(pg, x, y)
    pg.mouse.click(sx, sy)
    pg.wait_for_timeout(150)


def _rows(pg):
    return pg.evaluate("[...document.querySelectorAll('#cal-quick .q-row')].map(r=>r.innerText.replace(/\\s+/g,' ').trim())")


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
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            try:
                ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://127.0.0.1:{PORT}")
            except Exception:
                pass
            pg = ctx.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
            pg.evaluate("localStorage.setItem('calib.coachSeen','1');localStorage.removeItem('camscan.simplemode');"
                        "localStorage.removeItem('camscan.showPm')")
            pg.reload(wait_until="networkidle")

            # 1. The landing card: Quick measure, and the wallet card's link (which downloads).
            pg.wait_for_selector("#cal-landing-quick-btn", timeout=10000)
            href = pg.get_attribute("#cal-landing-card-link", "href")
            assert href and href.endswith("markers/camscan-wallet-card.svg"), f"card link: {href}"
            st = pg.evaluate("(h)=>fetch(h).then(r=>r.status)", href)
            assert st == 200, f"wallet card download: {st}"
            with pg.expect_file_chooser() as fc:
                pg.click("#cal-landing-quick-btn")
            fc.value.set_files(_photo())
            pg.wait_for_function("(()=>{const v=document.querySelector('.cal-view');return v&&v.__overlay&&"
                                 "v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()", timeout=20000)
            pg.wait_for_function("document.body.classList.contains('camscan-quick') && !!document.getElementById('cal-quick')",
                                 timeout=5000)
            # The unscaled photo opens the Set-scale chooser by itself; its backdrop closes it.
            chooser = '.cal-coach-card[aria-label="Set the scale"]'
            pg.wait_for_selector(chooser, state="visible", timeout=8000)
            pg.locator(".cal-coach").click(position={"x": 4, "y": 4})
            pg.wait_for_selector(chooser, state="detached", timeout=5000)

            # 2. Quick measure has the screen; unscaled, the panel says so.
            pg.wait_for_timeout(700)
            assert pg.evaluate("document.body.classList.contains('camscan-quick')"), "simple mode took the screen back"
            assert not pg.is_visible("#cal-simple"), "the simple card shows in Quick measure"
            assert "No scale yet" in pg.inner_text("#cal-quick-basis"), pg.inner_text("#cal-quick-basis")

            # 3. Scaled: the Measure tool is in hand and the basis is named.
            pg.evaluate(f"{OV}.setManualScaleFromPixels(400, 200, [[100,500],[500,500]])")
            pg.wait_for_function(f"{OV}.opts.mode==='segment'", timeout=5000)
            assert "square-on" in pg.inner_text("#cal-quick-basis"), pg.inner_text("#cal-quick-basis")

            # 4. Two taps: A, with its ±, and the label's letter; then B.
            _tap(pg, 220, 300)
            _tap(pg, 580, 300)
            pg.wait_for_function("document.querySelectorAll('#cal-quick .q-row').length===1", timeout=5000)
            rows = _rows(pg)
            # 0.5 mm/px and a 400 px scale line: ends ±0.71, scale ±0.64 (1 sigma) -> ± 1.9, shown 2.0.
            assert re.match(r"^A 180\.0 ± 2\.0 mm", rows[0]), f"row: {rows}"
            seg = pg.evaluate(f"(()=>{{const o={OV},a=o.ann.items.find(x=>x.type==='segment');return [a.qtag,o._readoutFor(a).text];}})()")
            assert seg[0] == "A" and "±" in seg[1], f"segment: {seg}"
            _tap(pg, 300, 240)
            _tap(pg, 300, 360)
            pg.wait_for_function("document.querySelectorAll('#cal-quick .q-row').length===2", timeout=5000)
            rows = _rows(pg)
            assert rows[1].startswith("B 60.0 ±"), f"rows: {rows}"

            # 5. Copy all; delete A.
            pg.click('#cal-quick button[data-q="copy"]')
            pg.wait_for_timeout(300)
            try:
                clip = pg.evaluate("navigator.clipboard.readText()")
            except Exception:
                clip = None
            if clip is not None:
                assert clip.startswith("A: 180.0 ±") and "\nB: 60.0 ±" in clip, f"clipboard: {clip!r}"
            pg.locator("#cal-quick button[aria-label='Delete A']").click()
            pg.wait_for_function("document.querySelectorAll('#cal-quick .q-row').length===1", timeout=5000)
            assert _rows(pg)[0].startswith("B "), _rows(pg)

            # 6. ± off in Settings drops it; ✓ Done goes back to the full tool.
            pg.evaluate("localStorage.setItem('camscan.showPm','0')")
            pg.wait_for_function("!document.querySelector('#cal-quick .q-row').innerText.includes('±')", timeout=5000)
            pg.click('#cal-quick button[data-q="done"]')
            pg.wait_for_function("!document.body.classList.contains('camscan-quick') && !document.getElementById('cal-quick')", timeout=5000)
            assert pg.evaluate(f"{OV}.opts.mode") == "select", "Done left the Measure tool in hand"
            assert pg.evaluate(f"{OV}.ann.items.filter(a=>a.type==='segment').length") == 1, "Done lost the measurements"

            assert not errors, f"page errors: {errors}"
            browser.close()
        print("E2E_RESULT: PASS (landing Quick measure opens the photo in it, wallet card downloads; simple mode stands "
              "aside; scale basis named; A/B lengths with ± on the list and labels; copy all; delete; ± off; Done)")
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
