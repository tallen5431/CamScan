"""Browser end-to-end test for building a 3D part from several views.

Drives the whole path a person takes, in Chromium, across three photos of one part:

  1. Top photo: auto-trace the outline, press 🧩 Use in 3D part, choose Top.
  2. Side photo (the toe is chamfered): trace, assign Side.
  3. End photo (the end profile with a bore through it): trace — the bore comes back as a
     hole — assign the bore as the Cavity and the outline as End, both on the SAME photo, so
     the cavity must keep its exact place.
  4. The 🧩 Build 3D part button in the top bar (it appears once a view is assigned): the
     preview draws all three views, the part is the expected size, the cavity is placed "as
     traced", and ticking Mirror downloads the opposite-hand part.
  5. The landing card says a part is in progress, and "Start a new part" clears it — so the
     next part does not inherit the last one's views.
  6. A photo with no scale: one "no scale yet" message at a time — the Set scale chooser, or
     the simple card with its own Set scale button — and the top banner steps aside for them
     and for the Set scale line tool.

Each step is glue between UI controls, the job store and the server, so this is where it can
break without any unit test noticing.

Not part of the fast smoke test (it needs Chromium + a running server). Run it directly:

    .venv/bin/python tests/test_part3d_e2e.py

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

MM_PER_PX = 0.5
L_MM, W_MM, H_MM = 200.0, 90.0, 25.0


def _img(polys_dark, polys_light=()):
    img = np.full((600, 800, 3), 200, np.uint8)
    for p in polys_dark:
        cv2.fillPoly(img, [np.round(np.asarray(p) * 256).astype(np.int32)], (50, 50, 50), shift=8)
    for p in polys_light:
        cv2.fillPoly(img, [np.round(np.asarray(p) * 256).astype(np.int32)], (200, 200, 200), shift=8)
    return img


def _to_px(poly_mm, x0, y0):
    """mm (u right, v up) -> image px, at MM_PER_PX, with the origin at image (x0, y0)."""
    return [[x0 + u / MM_PER_PX, y0 - v / MM_PER_PX] for u, v in poly_mm]


TOP = _to_px([[0, 0], [L_MM, 0], [L_MM, W_MM], [0, W_MM]], 200, 390)
SIDE = _to_px([[0, 0], [L_MM, 0], [L_MM, 15], [L_MM - 20, H_MM], [0, H_MM]], 200, 400)
END = _to_px([[0, 0], [W_MM, 0], [W_MM, 20], [W_MM - 5, H_MM], [5, H_MM], [0, 20]], 310, 420)
BORE = _to_px([[10, 4], [80, 4], [80, 16], [10, 16]], 310, 420)


def _data_url(img):
    _ok, enc = cv2.imencode(".png", img)
    return "data:image/png;base64," + base64.b64encode(enc.tobytes()).decode()


def _view(label, img):
    h, w = img.shape[:2]
    raw = _data_url(img)
    return {"label": label, "image": raw, "units": "mm",
            "scale": {"calibrated": True, "source": "manual line"}, "measurements": [],
            "restore": {"v": 1, "w": w, "h": h, "raw": raw,
                        "calib": {"markers": [], "image_size": {"width": w, "height": h},
                                  "marker_size_mm": None},
                        "ann": [], "units": "mm",
                        "manual": {"mmPerPx": MM_PER_PX, "homography": None, "ref": None}}}


def _bundle():
    views = [_view("Top", _img([TOP])), _view("Side", _img([SIDE])),
             _view("End", _img([END], [BORE]))]
    path = os.path.join(tempfile.gettempdir(), "part3d-e2e.camscan.json")
    json.dump({"kind": "camscan.job", "version": 1, "id": "part3d-e2e",
               "createdAt": "2026-01-01T00:00:00Z", "brief": {"part": "step pad"}, "views": views},
              open(path, "w"))
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


def _open_view(pg, i, first=False):
    if not first:
        pg.evaluate("window.CalibJob.open()")
    pg.wait_for_selector("#cal-job-panel", timeout=8000)
    pg.locator("#cal-job-panel").get_by_role("button", name="Open").nth(i).click()
    pg.wait_for_function(
        f"(()=>{{var v=document.querySelector('.cal-view');return v&&v.getAttribute('data-view-index')==='{i}'"
        "&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()", timeout=15000)


def _trace(pg, seed):
    pg.evaluate(f"(()=>{{const o={OV};o.setMode('outline');o.autoOutline({list(seed)});}})()")
    pg.wait_for_function(f"{OV}.ann.items.some(a=>a.type==='polyline'&&a.closed)", timeout=20000)


def _assign(pg, role):
    btn = pg.locator("#cal-ctx-3d")
    btn.wait_for(state="visible", timeout=5000)
    btn.click()
    pg.locator(f'#cal-part-pick [data-role="{role}"]').click()


def _plain_photo():
    """A photo with no calibration target in it."""
    img = np.full((600, 800, 3), 190, np.uint8)
    cv2.rectangle(img, (250, 200), (550, 400), (70, 70, 70), -1)
    path = os.path.join(tempfile.gettempdir(), "part3d-e2e-noscale.jpg")
    cv2.imwrite(path, img)
    return path


def main():
    bundle = _bundle()
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
            errors, dialogs = [], []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && window.CalibPart3D && document.querySelector('.landing-card')",
                                 timeout=15000)
            with pg.expect_file_chooser() as fc:
                pg.click("#cal-landing-load button")
            fc.value.set_files(bundle)

            # 1. Top
            _open_view(pg, 0, first=True)
            assert not pg.is_visible("#cal-tb-part"), "Build 3D part shown before any view is assigned"
            _trace(pg, (400, 300))
            _assign(pg, "top")
            # The way to the build is visible right away: on the toast, and in the top bar.
            pg.wait_for_selector(".cal-toast-action", timeout=5000)
            pg.wait_for_function("(()=>{const b=document.getElementById('cal-tb-part');"
                                 "return b&&b.offsetParent&&b.innerText.includes('(1)');})()", timeout=5000)
            # 2. Side
            _open_view(pg, 1)
            _trace(pg, (400, 376))
            _assign(pg, "side")
            # 3. End: the trace returns the outline AND the bore as a hole. Assign the outline as
            #    End, then switch to another photo and BACK before assigning the bore as the
            #    Cavity: switching re-captures the side's job record, and the photo's identity
            #    must survive that or the cavity stops counting as "same photo".
            _open_view(pg, 2)
            _trace(pg, (400, 380))
            pg.wait_for_function(f"{OV}.ann.items.filter(a=>a.type==='polyline'&&a.closed).length>=2",
                                 timeout=10000)

            def _select(nth):
                pg.evaluate(f"(()=>{{const o={OV};const ps=o.ann.items.filter(a=>a.type==='polyline'&&a.closed);"
                            f"o.ann.selectedId=ps[{nth}].id;o.setMode('select');o.requestDraw();}})()")
            _select(0)
            _assign(pg, "end")
            _open_view(pg, 0)
            _open_view(pg, 2)
            pg.wait_for_function(f"{OV}.ann.items.filter(a=>a.type==='polyline'&&a.closed).length>=2",
                                 timeout=10000)
            _select(1)
            # The picker says which roles are already filled, and from where.
            pg.locator("#cal-ctx-3d").click()
            end_txt = pg.inner_text('#cal-part-pick [data-role="end"]')
            top_txt = pg.inner_text('#cal-part-pick [data-role="top"]')
            cav_txt = pg.inner_text('#cal-part-pick [data-role="cavity"]')
            assert "already set" in end_txt and "this photo" in end_txt, f"end role: {end_txt!r}"
            assert "another photo" in top_txt, f"top role: {top_txt!r}"
            assert "already set" not in cav_txt, f"cavity role: {cav_txt!r}"
            pg.locator('#cal-part-pick [data-role="cavity"]').click()
            stored = pg.evaluate("(()=>{const v=window.CalibJob.part().views;"
                                 "return {roles:Object.keys(v).sort(), same:v.end.source===v.cavity.source,"
                                 "differ:v.top.source!==v.side.source};})()")
            assert stored["roles"] == ["cavity", "end", "side", "top"], f"views not stored: {stored}"
            assert stored["same"] and stored["differ"], f"photo identity wrong: {stored}"

            # 4. Build it from the top-bar button.
            assert "(4)" in pg.inner_text("#cal-tb-part"), pg.inner_text("#cal-tb-part")
            with pg.expect_response(lambda r: "/api/export/part" in r.url, timeout=20000) as pv:
                pg.click("#cal-tb-part")
            pj = pv.value.json()
            assert pj.get("ok"), f"preview refused: {pj}"
            info = pj["info"]
            size = info["size_mm"]
            for got, want, nm in zip(size, (L_MM, W_MM, H_MM), ("length", "width", "height")):
                assert abs(got - want) / want < 0.03, f"{nm} {got:.1f} mm, expected {want}"
            assert info.get("kind") == "cavity", f"no cavity cut: {info}"
            assert info.get("cavity_placement") == "as traced", f"same-photo cavity not kept in place: {info}"
            pg.wait_for_function("document.querySelectorAll('#cal-part-preview path[data-view]').length===3",
                                 timeout=8000)
            assert "Part:" in pg.inner_text("#cal-part-status"), "no part summary shown"

            # Mirror: the preview refreshes, and the download is the other-hand part.
            with pg.expect_response(lambda r: "/api/export/part" in r.url, timeout=20000):
                pg.check("#cal-part-mirror")
            pg.wait_for_function("document.getElementById('cal-part-status').innerText.includes('mirrored')",
                                 timeout=8000)
            with pg.expect_download(timeout=30000) as dl:
                with pg.expect_response(lambda r: "/api/export/part" in r.url, timeout=30000) as built:
                    pg.click("#cal-part-build")
            r = built.value
            assert r.status == 200, f"build failed: {r.status} {r.text()[:200]}"
            hinfo = json.loads(r.headers.get("x-camscan-info") or "{}")
            assert hinfo.get("mirrored") is True and hinfo.get("kind") == "cavity", f"built: {hinfo}"
            assert dl.value.suggested_filename == "part-mirrored.stl", dl.value.suggested_filename
            path = dl.value.path()
            blob = open(path, "rb").read()
            n = int.from_bytes(blob[80:84], "little") if len(blob) >= 84 else -1
            assert n > 0 and len(blob) == 84 + 50 * n, f"downloaded STL malformed: {len(blob)} bytes"
            pg.wait_for_function("document.getElementById('cal-part-status').innerText.includes('Saved')",
                                 timeout=8000)

            # The views survive a reload, as the job does.
            pg.reload(wait_until="networkidle")
            pg.wait_for_function("window.CalibJob", timeout=15000)
            kept = pg.evaluate("Object.keys(window.CalibJob.part().views).sort()")
            assert kept == ["cavity", "end", "side", "top"], f"views lost on reload: {kept}"

            # 5. The landing card says a part is in progress, and can start a new one.
            pg.wait_for_selector("#cal-landing-job", timeout=8000)
            note = pg.inner_text("#cal-landing-job")
            assert "3 photos" in note and "Top, Side, End, Cavity" in note, f"landing note: {note!r}"
            above = pg.evaluate("document.getElementById('cal-landing-job').nextElementSibling.id")
            assert above == "uploader", f"in-progress note is not above the photo drop zone: {above}"
            pg.evaluate("window.confirm = () => true")
            pg.click("#cal-landing-newpart")
            pg.wait_for_selector("#cal-landing-job", state="detached", timeout=5000)
            cleared = pg.evaluate("({n: window.CalibJob.count(), v: Object.keys(window.CalibJob.part().views).length})")
            assert cleared == {"n": 0, "v": 0}, f"new part not cleared: {cleared}"

            # 6. A photo with no scale. The Set scale chooser opens by itself, and the top banner
            #    saying the same thing does not sit on top of it. Close the chooser and the
            #    simple card carries the message with its own button; the banner still yields.
            pg.evaluate("localStorage.setItem('camscan.simplemode','1');localStorage.setItem('calib.coachSeen','1')")
            banner_js = ("(()=>{const b=document.getElementById('upload-error');"
                         "return {text:b.innerText, shown:getComputedStyle(b).display!=='none'};})()")
            panel = '.cal-coach-card[aria-label="Set the scale"]'
            pg.set_input_files("#uploader input[type=file]", _plain_photo())
            pg.wait_for_selector(panel, state="visible", timeout=20000)
            pg.wait_for_function("document.getElementById('upload-error').innerText.includes('No scale yet')",
                                 timeout=20000)
            assert not pg.evaluate(banner_js)["shown"], "banner over the Set scale chooser"
            pg.locator(".cal-coach").click(position={"x": 5, "y": 5})     # tap the backdrop to close
            pg.wait_for_selector(panel, state="detached", timeout=5000)
            pg.wait_for_selector("#cal-simple-scale", state="visible", timeout=5000)
            pg.evaluate("document.getElementById('upload-error').classList.remove('cal-dismissed')")
            assert not pg.evaluate(banner_js)["shown"], "banner stacked on the simple card's own message"
            pg.click("#cal-simple-scale")
            pg.wait_for_selector(panel, state="visible", timeout=5000)
            pg.locator(".cal-coach").click(position={"x": 5, "y": 5})     # tap the backdrop to close
            pg.wait_for_selector(panel, state="detached", timeout=5000)

            #    Mark-up mode (where the customer's screenshot was): a shown banner steps aside
            #    the moment the Set scale line tool is in use.
            pg.evaluate("document.getElementById('upload-error').classList.remove('cal-dismissed')")
            assert pg.evaluate(banner_js)["shown"], "banner not shown in mark-up mode"
            pg.evaluate(f"{OV}.setMode('setscale')")
            pg.wait_for_function(f"!{banner_js}.shown", timeout=5000)

            assert not errors, f"page errors: {errors}"
            assert not dialogs, f"unexpected alerts: {dialogs}"
            browser.close()
        print("E2E_RESULT: PASS (top/side/end/cavity assigned across 3 photos; part {:.1f}x{:.1f}x{:.1f} mm, "
              "cavity as traced; mirrored STL downloaded, {} triangles; views survive reload; "
              "landing offers a new part; scale notice steps aside)".format(
                  size[0], size[1], size[2], n))
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
