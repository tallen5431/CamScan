"""Browser end-to-end regression for multi-view capture (one image per view).

Reproduces the reported bug: upload photo A, capture it as a view, upload photo B, capture
it as a view — and the submission ended up holding photo A for BOTH views. Root cause: Dash
reconciles the #viewer subtree in place on each upload (the .cal-view node is REUSED, only
its data-img changes), so keying the overlay boot on :not([data-initialized]) left the first
overlay — and its FIRST image — mounted, and every "Add another photo" re-captured that first
photo.

This asserts the two stored view records hold DISTINCT images (A then B), and that the live
overlay swaps to B after the second upload.

Not part of the fast smoke test (needs Chromium + a running server). Run it directly:

    .venv/bin/python tests/test_multiview_e2e.py

Skips cleanly (exit 0) where Playwright / a browser isn't available.
"""
import os
import sys
import time
import tempfile
import subprocess
import urllib.request
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PORT = os.getenv("CAMSCAN_E2E_PORT", "8200")


def _skip(msg):
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


def _two_distinct_images():
    # Two solid, visually distinct photos (BGR). Kept simple so the mean-colour decode below
    # is unambiguous: A reads red-dominant, B reads blue-dominant.
    a = np.full((300, 400, 3), (40, 40, 220), np.uint8)   # red-ish
    b = np.full((300, 400, 3), (220, 60, 40), np.uint8)   # blue-ish
    pa = os.path.join(tempfile.gettempdir(), "mv-A.jpg"); cv2.imwrite(pa, a)
    pb = os.path.join(tempfile.gettempdir(), "mv-B.jpg"); cv2.imwrite(pb, b)
    return pa, pb


def _wait_server(timeout=40):
    for _ in range(timeout * 2):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).status == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


# Decode the mean colour of the current overlay image (in-page, 8x8 downscale).
_OVERLAY_MEAN = """(async()=>{
  var v=document.querySelector('.cal-view'); var o=v&&v.__overlay; if(!o||!o.img) return null;
  var c=document.createElement('canvas'); c.width=8;c.height=8;
  c.getContext('2d').drawImage(o.img,0,0,8,8);
  var d=c.getContext('2d').getImageData(0,0,8,8).data; var r=0,b=0,n=d.length/4;
  for(var i=0;i<d.length;i+=4){r+=d[i];b+=d[i+2];}
  return {r:Math.round(r/n), b:Math.round(b/n)};
})()"""

# Decode the mean colour of each STORED view image (localStorage is the source of truth —
# it holds every captured record and survives the reload between photos).
_STORED_MEANS = """(async()=>{
  var j={}; try{ j=JSON.parse(localStorage.getItem('camscan.job.v1'))||{}; }catch(e){}
  var views=(j&&j.views)||[];
  function meanOf(src){return new Promise(res=>{ if(!src){res(null);return;}
    var im=new Image();
    im.onload=function(){ var c=document.createElement('canvas'); c.width=8;c.height=8;
      c.getContext('2d').drawImage(im,0,0,8,8);
      var d=c.getContext('2d').getImageData(0,0,8,8).data; var r=0,b=0,n=d.length/4;
      for(var i=0;i<d.length;i+=4){r+=d[i];b+=d[i+2];} res({r:Math.round(r/n), b:Math.round(b/n)}); };
    im.onerror=function(){ res(null); }; im.src=src; });}
  var out=[]; for(var v of views){ out.push(await meanOf(v.image)); } return out;
})()"""

_OVERLAY_READY = ("(()=>{var v=document.querySelector('.cal-view');"
                  "return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()")


def main():
    pa, pb = _two_distinct_images()
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
            pg.wait_for_function("window.CalibJob && document.querySelector('#uploader input[type=file]')", timeout=15000)
            # Start clean so a stale job from a prior run can't mask a regression.
            pg.evaluate("try{localStorage.removeItem('camscan.job.v1');}catch(e){}")

            # Upload photo A, wait for the overlay to mount it, capture it as a view.
            pg.set_input_files("#uploader input[type=file]", pa)
            pg.wait_for_function(_OVERLAY_READY, timeout=15000)
            col_a = pg.evaluate(_OVERLAY_MEAN)
            assert col_a and col_a["r"] > col_a["b"], f"overlay after A should be red-dominant: {col_a}"
            pg.evaluate("window.CalibJob.addView('Top')")

            # Upload photo B — Dash reuses the .cal-view node, only data-img changes.
            pg.set_input_files("#uploader input[type=file]", pb)
            # The overlay must REBOOT onto B: wait until the mounted image reads blue-dominant,
            # not merely until an overlay exists (the stale one would still be red).
            pg.wait_for_function(
                "(()=>{var v=document.querySelector('.cal-view');var o=v&&v.__overlay;"
                "if(!o||!o.img||!o.img.naturalWidth) return false;"
                "var c=document.createElement('canvas');c.width=8;c.height=8;"
                "c.getContext('2d').drawImage(o.img,0,0,8,8);"
                "var d=c.getContext('2d').getImageData(0,0,8,8).data;var r=0,b=0,n=d.length/4;"
                "for(var i=0;i<d.length;i+=4){r+=d[i];b+=d[i+2];} return (b/n)>(r/n);})()",
                timeout=15000)
            col_b = pg.evaluate(_OVERLAY_MEAN)
            assert col_b and col_b["b"] > col_b["r"], f"overlay after B should swap to blue (not stale A): {col_b}"
            pg.evaluate("window.CalibJob.addView('Front')")

            count = pg.evaluate("window.CalibJob.count()")
            assert count == 2, f"expected two collected views, got {count}"

            stored = pg.evaluate(_STORED_MEANS)
            assert len(stored) == 2 and stored[0] and stored[1], f"two stored view images expected: {stored}"
            v0_red = stored[0]["r"] > stored[0]["b"]
            v1_blue = stored[1]["b"] > stored[1]["r"]
            distinct = abs(stored[0]["r"] - stored[1]["r"]) > 40 or abs(stored[0]["b"] - stored[1]["b"]) > 40
            assert v0_red, f"view 0 must keep photo A (red): {stored}"
            assert v1_blue, f"view 1 must hold photo B (blue), not a duplicate of A: {stored}"
            assert distinct, f"the two views must store DISTINCT images: {stored}"

            assert not errors, f"page errors: {errors}"
            browser.close()
        print(f"E2E_RESULT: PASS (two uploads -> two distinct stored views A={stored[0]} B={stored[1]})")
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
