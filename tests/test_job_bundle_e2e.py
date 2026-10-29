"""Browser end-to-end test: the 3D part is saved in, and loaded from, the job file.

💾 Save job used to write every photo but not the 3D part assembled from them, so a job
opened on another device (or by Datum, from a submission) had its photos and no part. Now:
  - loading a job file restores its 3D part: the views (with their alignment) and the Build
    settings, and each view still names the photo it was traced on, so opening that photo
    shows the view following its outline (not "photo removed");
  - saving writes the part back, with each photo's key;
  - an older job file with no part loads as before, with an empty part.

    .venv/bin/python tests/test_job_bundle_e2e.py

It SKIPS cleanly (exit 0) where Playwright or a browser isn't available — unless
CAMSCAN_E2E_REQUIRE=1, as CI sets it.
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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8211")


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

TOP = [[60, 60], [320, 60], [320, 240], [60, 240]]
PART = {"views": {"top": {"points": [[0, 0], [130, 0], [130, 90], [0, 90]], "mm_per_px": 0.5, "image_height": 300,
                          "source": "photo-a", "ann_id": 7, "rot": 1},
                  "end": {"points": [[0, 0], [90, 0], [90, 25], [0, 25]], "mm_per_px": 0.5, "image_height": 300,
                          "source": "photo-b"}},
        "opts": {"wall": 3, "known": {"length": 130}}}


def _bundle(name, with_part):
    img = np.full((300, 400, 3), 180, np.uint8)
    cv2.rectangle(img, (60, 60), (320, 240), (60, 62, 66), -1)
    raw = "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", img)[1].tobytes()).decode()

    def view(label, key, ann):
        return {"label": label, "image": raw, "units": "mm", "scale": {"calibrated": True}, "measurements": [],
                "photoKey": key,
                "restore": {"v": 1, "w": 400, "h": 300, "raw": raw,
                            "calib": {"markers": [], "image_size": {"width": 400, "height": 300}},
                            "ann": ann, "units": "mm", "manual": {"mmPerPx": 0.5, "homography": None, "ref": None}}}
    outline = [{"id": 7, "type": "polyline", "pts": TOP, "closed": True, "mm_per_px": 0.5, "units": "mm",
                "markerId": None}]
    b = {"kind": "camscan.job", "version": 1, "id": "job-bundle", "createdAt": "2026-01-01T00:00:00Z",
         "brief": {"part": "step pad"}, "views": [view("Top", "photo-a", outline), view("End", "photo-b", [])]}
    if with_part:
        b["part3d"] = PART
    path = os.path.join(tempfile.gettempdir(), name)
    json.dump(b, open(path, "w"))
    return path


def _wait_server(timeout=40):
    for _ in range(timeout * 2):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).status == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


def _load(pg, path):
    pg.evaluate("()=>{ if(!document.getElementById('cal-job-panel')) window.CalibJob.open(); }")
    pg.wait_for_selector("#cal-job-panel", timeout=8000)
    with pg.expect_file_chooser() as fc:
        pg.locator("#cal-job-panel").get_by_role("button", name="⤓ Load job").click()
    fc.value.set_files(path)


def main():
    with_part, legacy = _bundle("e2e-part.camscan.json", True), _bundle("e2e-legacy.camscan.json", False)
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
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
            pg = ctx.new_page()
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && window.CalibPart3D && document.querySelector('.landing-card')",
                                 timeout=15000)

            # 1. Loading a job file restores its 3D part, and says so.
            pg.wait_for_selector("#cal-landing-load", timeout=8000)
            with pg.expect_file_chooser() as fc:
                pg.click("#cal-landing-load button")
            fc.value.set_files(with_part)
            pg.wait_for_selector("#cal-job-panel", timeout=8000)
            pg.wait_for_function("document.getElementById('cal-job-panel').innerText.includes('3D part (Top, End)')",
                                 timeout=5000)
            got = pg.evaluate("(()=>{const p=window.CalibJob.part();return {roles:Object.keys(p.views).sort(),"
                              "rot:p.views.top.rot, src:p.views.top.source, wall:p.opts.wall, known:p.opts.known};})()")
            assert got == {"roles": ["end", "top"], "rot": 1, "src": "photo-a", "wall": 3,
                           "known": {"length": 130}}, f"part not restored: {got}"

            # 2. Each view still finds its photo: opened, Top's photo is the one Top names, and
            #    the Build dialog shows it following its outline — not "photo removed".
            pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()
            pg.wait_for_function("(()=>{var v=document.querySelector('.cal-view');return v&&"
                                 "v.getAttribute('data-view-index')==='0'&&v.__overlay&&v.__overlay.img&&"
                                 "v.__overlay.img.naturalWidth>0&&v.__overlay.ann.items.length===1;})()", timeout=15000)
            assert pg.evaluate("window.CalibJob.photoId()") == "photo-a", "the loaded Top photo lost its key"
            pg.evaluate("window.CalibPart3D.open()")
            pg.wait_for_selector("#cal-part-row-top", timeout=8000)
            row = pg.inner_text("#cal-part-row-top")
            assert "removed" not in row and "deleted" not in row, f"Top row after load: {row!r}"

            # 3. Saving writes the part back, with each photo's key.
            pg.click("#cal-part-panel button[aria-label='Close']")
            pg.evaluate("window.CalibJob.open()")
            pg.wait_for_selector("#cal-job-panel", timeout=8000)
            with pg.expect_download(timeout=15000) as dl:
                pg.locator("#cal-job-panel").get_by_role("button", name="💾 Save job").click()
            saved = json.load(open(dl.value.path()))
            sp = saved.get("part3d") or {}
            assert sorted(sp.get("views", {})) == ["end", "top"] and sp.get("opts", {}).get("wall") == 3, \
                f"saved part3d: {str(sp)[:200]}"
            assert sp["views"]["top"]["source"] == "photo-a" and sp["views"]["top"].get("rot") == 1, sp["views"]["top"]
            assert [v.get("photoKey") for v in saved["views"]] == ["photo-a", "photo-b"], \
                [v.get("photoKey") for v in saved["views"]]

            # 4. An older job file, with no part in it, loads with an empty part.
            _load(pg, legacy)
            pg.wait_for_function("document.getElementById('cal-job-panel').innerText.includes('Loaded 2 views')",
                                 timeout=5000)
            assert pg.evaluate("Object.keys(window.CalibJob.part().views).length") == 0, "a legacy job kept a part"

            assert not errors, f"page errors: {errors}"
            browser.close()
        print("E2E_RESULT: PASS (job file restores the 3D part (views, alignment, settings); the loaded Top still "
              "finds its photo; Save job writes the part and each photo's key back; a legacy job loads with no part)")
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
