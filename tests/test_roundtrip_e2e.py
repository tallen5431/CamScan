"""Browser end-to-end test for the reloadable job round-trip.

Boots the real CamScan app, loads a `.camscan.json` job through the file picker, opens a
side, and asserts the overlay re-hydrates with the customer's annotation + scale restored
(no Dash reload) — then re-captures a snapshot from the live overlay so BOTH directions of
the round-trip are exercised against the real JavaScript.

Not part of the fast smoke test (it needs Chromium + a running server). Run it directly:

    .venv/bin/python tests/test_roundtrip_e2e.py

It SKIPS cleanly (exit 0) where Playwright or a browser isn't available, so it never breaks
an environment that can't run a browser.
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
    print(f"E2E_RESULT: SKIP ({msg})")
    sys.exit(0)


try:
    import cv2
    import numpy as np
    from playwright.sync_api import sync_playwright
except Exception as e:  # pragma: no cover - environment without deps
    _skip(f"missing dependency: {e}")

# Resolve a browser: prefer the environment's pre-installed Chromium, else Playwright's own.
_CHROMIUM = None
for cand in (os.getenv("PLAYWRIGHT_CHROMIUM"), "/opt/pw-browsers/chromium"):
    if cand and os.path.exists(cand):
        _CHROMIUM = cand
        break


def _make_bundle_file():
    img = np.full((300, 400, 3), 180, np.uint8)
    cv2.rectangle(img, (60, 60), (320, 240), (60, 62, 66), -1)
    _ok, enc = cv2.imencode(".jpg", img)
    raw = "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode()
    def _view(label, mmpp, ann):
        return {
            "label": label, "image": raw, "units": "mm",
            "scale": {"calibrated": True, "source": "manual line"},
            "measurements": [], "restore": {
                "v": 1, "w": 400, "h": 300, "raw": raw,
                "calib": {"markers": [], "image_size": {"width": 400, "height": 300}, "marker_size_mm": None},
                "ann": ann, "units": "mm", "manual": {"mmPerPx": mmpp, "homography": None, "ref": None},
            },
        }
    bundle = {
        "kind": "camscan.job", "version": 1, "id": "job-e2e", "createdAt": "2026-01-01T00:00:00Z",
        "brief": {"part": "e2e bracket", "material": "aluminium", "contact": "x@y.com"},
        "views": [
            _view("Top", 0.1, [{"id": 1, "type": "polyline", "pts": [[60, 60], [320, 60], [320, 240], [60, 240]],
                                "closed": True, "mm_per_px": 0.1, "units": "mm", "markerId": None}]),
            _view("Front", 0.2, []),
        ],
    }
    path = os.path.join(tempfile.gettempdir(), "e2e.camscan.json")
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


def main():
    bundle_path = _make_bundle_file()
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
            pg.wait_for_selector("#cal-landing-load", timeout=8000)

            with pg.expect_file_chooser() as fc:
                pg.click("#cal-landing-load button")
            fc.value.set_files(bundle_path)

            pg.wait_for_selector("#cal-job-panel", timeout=8000)
            assert "Collected views (2)" in pg.inner_text("#cal-job-panel"), "views not loaded into panel"
            pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()

            pg.wait_for_function(
                "(()=>{var v=document.querySelector('.cal-view');"
                "return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0"
                "&&v.__overlay.ann.items.length===1;})()", timeout=15000)
            state = pg.evaluate(
                "(()=>{var o=document.querySelector('.cal-view').__overlay;"
                "return {n:o.ann.items.length, type:o.ann.items[0].type, closed:o.ann.items[0].closed,"
                "mmpp:o.opts.manualMmPerPx, units:o.opts.units, calibrated:o.isCalibrated()};})()")
            assert state["n"] == 1 and state["type"] == "polyline" and state["closed"] is True, f"annotation not restored: {state}"
            assert abs(state["mmpp"] - 0.1) < 1e-9, f"manual scale not restored: {state}"
            assert state["units"] == "mm" and state["calibrated"] is True, f"scale/units not restored: {state}"
            # A reopened side already has scale — the first-run measuring coach is suppressed.
            assert pg.query_selector(".cal-coach") is None, "measuring coach should not show on a restored side"

            cap = pg.evaluate(
                "(()=>{var o=document.querySelector('.cal-view').__overlay;var r=o.getRestoreState(1600);"
                "return r?{raw:(''+r.raw).indexOf('data:image')===0, ann:r.ann.length, hasCalib:!!r.calib,"
                "mmpp:r.manual.mmPerPx}:null;})()")
            assert cap and cap["raw"] and cap["ann"] == 1 and cap["hasCalib"], f"capture snapshot malformed: {cap}"
            assert abs(cap["mmpp"] - 0.1) < 1e-9, f"capture lost manual scale: {cap}"

            # 6) side switcher: flip to another side and back; edits to a side must persist.
            pg.wait_for_selector("#cal-side-switcher", timeout=8000)
            # Add the edit via the real annotation store (issues an id) — this also exercises
            # the id-counter reservation on restore: the new mark must NOT collide with the
            # restored polyline's id.
            pg.evaluate("(()=>{var o=document.querySelector('.cal-view').__overlay;"
                        "window.CalibAnn.addNote(o.ann,[120,120],'edit');o.requestDraw();})()")
            pg.locator("#cal-side-switcher").get_by_role("button", name="Front").click()
            pg.wait_for_function(
                "(()=>{var v=document.querySelector('.cal-view');return v&&v.getAttribute('data-view-index')==='1'"
                "&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0"
                "&&Math.abs(v.__overlay.opts.manualMmPerPx-0.2)<1e-9;})()", timeout=15000)
            front = pg.evaluate("document.querySelector('.cal-view').__overlay.ann.items.length")
            assert front == 0, f"Front should have no annotations, got {front}"
            pg.locator("#cal-side-switcher").get_by_role("button", name="Top").click()
            pg.wait_for_function(
                "(()=>{var v=document.querySelector('.cal-view');return v&&v.getAttribute('data-view-index')==='0'"
                "&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0"
                "&&v.__overlay.ann.items.length===2;})()", timeout=15000)   # original + the edit persisted

            # id-collision guard (restore must advance CalibAnn's id counter).
            ids = pg.evaluate("document.querySelector('.cal-view').__overlay.ann.items.map(a=>a.id)")
            assert len(set(ids)) == len(ids), f"annotation id collision after restore: {ids}"

            # Re-capturing the reopened side must UPDATE in place, not push a duplicate.
            pg.evaluate("window.CalibJob.addView('Top')")
            count = pg.evaluate("window.CalibJob.count()")
            assert count == 2, f"re-adding a reopened side should update in place, not duplicate (count={count})"

            assert not errors, f"page errors: {errors}"
            browser.close()
        print("E2E_RESULT: PASS (load; reopen editable; side-switch persists edits; live re-capture ok)")
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
