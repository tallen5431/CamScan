"""Browser end-to-end regression: loading a job while a reopened side is on screen.

Reproduces the reported HIGH bug: after opening a side of job A (which tags the on-screen
overlay with a positional data-view-index), loading job B left that stale tag in place, so the
next Open/save/pagehide sync wrote A's image+annotations over one of B's freshly-loaded views —
silent, persistable corruption of the loaded customer job.

This loads job A, opens its side, loads job B, opens a B side, and asserts B's views still hold
B's own content (distinct label + image), i.e. nothing from A bled across.

Not part of the fast smoke test (needs Chromium + a running server). Run it directly:

    .venv/bin/python tests/test_jobswap_e2e.py

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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8201")


def _skip(msg):
    print(f"E2E_RESULT: SKIP ({msg})")
    sys.exit(0)


try:
    import cv2
    import numpy as np
    from playwright.sync_api import sync_playwright
except Exception as e:  # pragma: no cover
    _skip(f"missing dependency: {e}")

_CHROMIUM = None
for cand in (os.getenv("PLAYWRIGHT_CHROMIUM"), "/opt/pw-browsers/chromium"):
    if cand and os.path.exists(cand):
        _CHROMIUM = cand
        break


def _bundle(path, label, bgr):
    """A one-view reloadable job whose image is a solid colour (so a cross-contamination shows
    up as a mean-colour swap) and whose view carries a distinctive label."""
    img = np.full((300, 400, 3), bgr, np.uint8)
    _ok, enc = cv2.imencode(".jpg", img)
    raw = "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode()
    bundle = {
        "kind": "camscan.job", "version": 1, "id": f"job-{label}", "createdAt": "2026-01-01T00:00:00Z",
        "brief": {"part": label},
        "views": [{
            "label": label, "image": raw, "units": "mm",
            "scale": {"calibrated": True, "source": "manual line"}, "measurements": [],
            "restore": {"v": 1, "w": 400, "h": 300, "raw": raw,
                        "calib": {"markers": [], "image_size": {"width": 400, "height": 300}, "marker_size_mm": None},
                        "ann": [], "units": "mm", "manual": {"mmPerPx": 0.1, "homography": None, "ref": None}},
        }],
    }
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


# Mean (r,b) of view 0's stored image in localStorage — the source of truth that persists.
_VIEW0 = """(async()=>{
  var j={}; try{ j=JSON.parse(localStorage.getItem('camscan.job.v1'))||{}; }catch(e){}
  var v=(j.views||[])[0]; if(!v) return null;
  function mean(src){return new Promise(res=>{var im=new Image();im.onload=function(){
    var c=document.createElement('canvas');c.width=8;c.height=8;c.getContext('2d').drawImage(im,0,0,8,8);
    var d=c.getContext('2d').getImageData(0,0,8,8).data;var r=0,b=0,n=d.length/4;
    for(var i=0;i<d.length;i+=4){r+=d[i];b+=d[i+2];}res({r:Math.round(r/n),b:Math.round(b/n)});};
    im.onerror=function(){res(null);};im.src=src;});}
  return {label:v.label, color: await mean(v.image)};
})()"""


def main():
    a = _bundle(os.path.join(tempfile.gettempdir(), "jobA.camscan.json"), "Top", (40, 40, 220))    # red
    b = _bundle(os.path.join(tempfile.gettempdir(), "jobB.camscan.json"), "Alpha", (220, 60, 40))  # blue
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
            pg.evaluate("try{localStorage.removeItem('camscan.job.v1');}catch(e){}")

            # Load job A via the landing card.
            with pg.expect_file_chooser() as fc:
                pg.click("#cal-landing-load button")
            fc.value.set_files(a)
            pg.wait_for_selector("#cal-job-panel", timeout=8000)

            # Open its side (tags the on-screen overlay with data-view-index="0").
            pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()
            pg.wait_for_function(
                "(()=>{var v=document.querySelector('.cal-view');return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0"
                "&&v.getAttribute('data-view-index')==='0';})()", timeout=15000)

            # Now reopen the panel and load job B via its "Load job" button (the reported path).
            # The fix must strip the stale positional tag left by A's open side.
            pg.evaluate("window.CalibJob.open()")
            pg.wait_for_selector("#cal-job-panel", timeout=8000)
            with pg.expect_file_chooser() as fc:
                pg.locator("#cal-job-panel").get_by_role("button", name="Load job").first.click()
            fc.value.set_files(b)
            pg.wait_for_selector("#cal-job-panel", timeout=8000)
            tag = pg.evaluate("(()=>{var v=document.querySelector('.cal-view');return v?v.getAttribute('data-view-index'):'none';})()")
            assert tag in (None, "none"), f"stale data-view-index survived job load: {tag!r}"

            # Open one of B's views, then drive a save (the corruption path ran on sync/save).
            pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()
            pg.wait_for_function(
                "(()=>{var v=document.querySelector('.cal-view');return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()",
                timeout=15000)
            pg.evaluate("window.CalibJob.syncOpen && window.CalibJob.syncOpen()")

            v0 = pg.evaluate(_VIEW0)
            assert v0 and v0["color"], f"could not read stored view 0: {v0}"
            # B's view 0 must still be B: label 'Alpha' and blue — not A's 'Top'/red bled across.
            assert v0["label"] == "Alpha", f"loaded job B's view was relabeled from A: {v0}"
            assert v0["color"]["b"] > v0["color"]["r"], f"job B's view image was overwritten by A (red): {v0}"

            assert not errors, f"page errors: {errors}"
            browser.close()
        print(f"E2E_RESULT: PASS (job B intact after load-over-reopened-side: {v0})")
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
