"""Browser end-to-end test: a photo the user worked on is never lost, and the simple card
leads to the 3D part.

A fresh upload used to join the job only when a label chip was tapped, so a scaled, traced
photo whose outline was the part's Top view vanished on a reload, on New, or on a tap on
another side — the part kept the outline, but the photo could never be reopened to fix it.
This drives, in Chromium:

  1. Fresh photo: scale it, trace it, assign Top, add a measurement, reload. The photo is
     stored once (capturing twice adds nothing, and its photoId never changes), and reopening
     it brings the measurement back. Re-assigning Top on the same photo keeps its rotation.
     The job panel shows the 3D part row, ticks Top in Suggested shots, and its Submit button
     is not drawn in the accent fill. Removing the photo asks first and flags the Top view.
  2. Side switch: a 3-side job is open, a fresh photo is assigned as End, and a tap on the
     switcher's Top keeps it — 4 sides, and End's photo is one of them.
  3. New (via CalibJob.beforeReload, which the button calls): a fresh photo with an outline
     is stored before the page goes.
  4. Simple mode: the top bar's Set scale chip, then two taps on a ruler near the bottom of
     the photo — where the card used to sit and swallow the taps — set the scale. The card
     words the scale by where it came from (a ruler line is not "Marker found"), offers
     Top / Side / End, and leads with Build once a view is assigned.

Not part of the fast smoke test (needs Chromium + a running server). Run it directly:

    .venv/bin/python tests/test_job_persist_e2e.py

Skips cleanly (exit 0) where Playwright / a browser isn't available, unless
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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8203")


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
except Exception as e:  # pragma: no cover
    _skip(f"missing dependency: {e}")

_CHROMIUM = None
for cand in (os.getenv("PLAYWRIGHT_CHROMIUM"), "/opt/pw-browsers/chromium"):
    if cand and os.path.exists(cand):
        _CHROMIUM = cand
        break

OV = "document.querySelector('.cal-view').__overlay"
RULER_Y = 1150          # the ruler lies across the bottom of a 1200 x 1600 portrait photo
RULER_X = (300, 900)    # two marks 600 px apart: 300 mm at 0.5 mm/px


def _photo(name, shade=60):
    """A dark part on a light floor with a ruler lying across the bottom, as the user shoots
    it. `shade` makes each photo distinct, so one cannot stand in for another."""
    img = np.full((1600, 1200, 3), 185, np.uint8)
    cv2.rectangle(img, (350, 250), (850, 1000), (shade, shade, shade), -1)
    cv2.rectangle(img, (150, RULER_Y - 22), (1050, RULER_Y + 22), (120, 215, 240), -1)
    for x in range(150, 1051, 20):
        cv2.line(img, (x, RULER_Y - 22), (x, RULER_Y - (2 if x % 100 else -8)), (20, 20, 20), 2)
    path = os.path.join(tempfile.gettempdir(), f"job-persist-{name}.jpg")
    cv2.imwrite(path, img)
    return path


def _view(label, shade):
    img = np.full((600, 800, 3), 200, np.uint8)
    cv2.rectangle(img, (200, 200), (600, 400), (shade, shade, shade), -1)
    _ok, enc = cv2.imencode(".png", img)
    raw = "data:image/png;base64," + base64.b64encode(enc.tobytes()).decode()
    return {"label": label, "image": raw, "units": "mm",
            "scale": {"calibrated": True, "source": "manual line"}, "measurements": [],
            "restore": {"v": 1, "w": 800, "h": 600, "raw": raw,
                        "calib": {"markers": [], "image_size": {"width": 800, "height": 600},
                                  "marker_size_mm": None},
                        "ann": [], "units": "mm",
                        "manual": {"mmPerPx": 0.5, "homography": None, "ref": None}}}


def _bundle():
    path = os.path.join(tempfile.gettempdir(), "job-persist-e2e.camscan.json")
    json.dump({"kind": "camscan.job", "version": 1, "id": "job-persist", "createdAt": "2026-01-01T00:00:00Z",
               "brief": {"part": "step pad"},
               "views": [_view("Top", 40), _view("Side", 70), _view("Detail", 100)]}, open(path, "w"))
    return path


def _wait_server(timeout=40):
    for _ in range(timeout * 2):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=2).status == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


TO_SCREEN = (
    "(([ix,iy])=>{const o=" + OV + ";const c=o.canvas;const [cx,cy]=o.vp.imageToCanvas(ix,iy);"
    "const r=c.getBoundingClientRect();const cs=getComputedStyle(c);"
    "const bl=parseFloat(cs.borderLeftWidth)||0,bt=parseFloat(cs.borderTopWidth)||0;"
    "const pl=parseFloat(cs.paddingLeft)||0,pt=parseFloat(cs.paddingTop)||0;"
    "const cw=o.vp._cw||r.width,ch=o.vp._ch||r.height;"
    "const x=r.left+bl+pl+cx*cw/c.width,y=r.top+bt+pt+cy*ch/c.height;"
    "const top=document.elementFromPoint(x,y);"
    "return {x,y,onCanvas:top===c,top:top?(top.id||top.className||top.tagName):null};})"
)
READY = ("(()=>{var v=document.querySelector('.cal-view');"
         "return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()")
# A ruler-line scale, the way the line tool and its length box set one.
SCALE = f"{OV}.setManualScaleFromPixels(600, 300, [[{RULER_X[0]},{RULER_Y}],[{RULER_X[1]},{RULER_Y}]])"
# A closed outline round the part, selected — what a trace leaves behind.
OUTLINE_STMT = (f"{{const o={OV};window.CalibAnn.addPolyline(o.ann,[[350,250],[850,250],[850,1000],[350,1000]],"
                "o.getScale()||0,o.opts.units,null,true);o.setMode('select');o.requestDraw();}")
OUTLINE = "(()=>" + OUTLINE_STMT + ")()"


def _context(browser, errors, dialogs, simple=False, viewport=None):
    ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    ctx.add_init_script("try{localStorage.setItem('calib.coachSeen','1');"
                        f"localStorage.setItem('camscan.simplemode','{1 if simple else 0}');}}catch(e){{}}")
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && window.CalibPart3D && document.querySelector('#uploader input[type=file]')",
                         timeout=15000)
    pg.evaluate("window.CalibJob.ready")
    return ctx, pg


def _upload(pg, path):
    pg.set_input_files("#uploader input[type=file]", path)
    pg.wait_for_function("(()=>{const v=document.querySelector('.cal-view');return v&&v.__overlay&&v.__overlay.img"
                         "&&v.__overlay.img.naturalWidth>0&&(v.getAttribute('data-json')||'').indexOf('/uploads/')>=0"
                         "&&v.__bootedImg===v.getAttribute('data-img');})()", timeout=20000)
    # The Set scale chooser opens by itself on a photo with no scale; close it.
    try:
        pg.wait_for_selector('.cal-coach-card[aria-label="Set the scale"]', state="visible", timeout=4000)
        pg.locator(".cal-coach").click(position={"x": 5, "y": 5})
        pg.wait_for_selector('.cal-coach-card[aria-label="Set the scale"]', state="detached", timeout=5000)
    except Exception:
        pass


def scenario_fresh_photo(browser, errors, dialogs):
    ctx, pg = _context(browser, errors, dialogs)
    _upload(pg, _photo("top"))
    pid0 = pg.evaluate("window.CalibJob.photoId()")
    assert pg.evaluate("window.CalibJob.count()") == 0, "an untouched photo was stored"
    # Setting a scale is something done to the photo: it is kept on its own, without a chip.
    pg.evaluate(SCALE)
    pg.wait_for_function("window.CalibJob.count()===1", timeout=8000)
    pg.evaluate(OUTLINE)
    # What 🧩 Use in 3D part calls (P2 contract), twice: no duplicate, same identity.
    pg.evaluate("window.CalibJob.captureCurrent('Top')")
    pg.evaluate("window.CalibJob.captureCurrent('Top')")
    state = pg.evaluate("({n:window.CalibJob.count(), pid:window.CalibJob.photoId(), stored:window.CalibJob.isCurrentStored()})")
    assert state == {"n": 1, "pid": pid0, "stored": True}, f"capture: {state} (photoId before {pid0})"
    assert pg.evaluate("window.CalibPart3D.assign('top')"), "assign Top refused"
    src = pg.evaluate("window.CalibJob.part().views.top.source")
    assert src == pid0, f"Top's source {src} is not this photo {pid0}"

    # Re-assigning Top on the same photo keeps the alignment turned in the Build dialog.
    pg.evaluate("(()=>{const v=window.CalibJob.part().views.top;v.rot=1;v.flip_h=true;})()")
    pg.evaluate("window.CalibPart3D.assign('top')")
    kept = pg.evaluate("(()=>{const v=window.CalibJob.part().views.top;return [v.rot,v.flip_h];})()")
    assert kept == [1, True], f"re-assign on the same photo reset the alignment: {kept}"

    # A measurement made after the photo was stored is autosaved with it.
    pg.evaluate(f"(()=>{{const o={OV};window.CalibAnn.addSegment(o.ann,[350,1100],[850,1100],o.getScale(),o.opts.units,null);"
                "o.requestDraw();})()")
    deadline = time.time() + 8
    stored = None
    while time.time() < deadline:
        stored = pg.evaluate("(async()=>{await window.CalibJob.flush();const j=await window.CalibJob.persisted();"
                             "const v=j&&j.views[0];return v?{n:j.views.length,label:v.label,"
                             "seg:(v.restore.ann||[]).filter(a=>a.type==='segment').length}:null;})()")
        if stored and stored["seg"] == 1:
            break
        time.sleep(0.4)
    assert stored == {"n": 1, "label": "Top", "seg": 1}, f"measurement not autosaved: {stored}"

    # The job panel: the 3D part row and its Build chip, Top ticked, Submit not the accent.
    pg.evaluate("window.CalibJob.open()")
    pg.wait_for_selector("#cal-job-panel #cal-job-part", timeout=5000)
    row = pg.inner_text("#cal-job-part")
    assert "Top ✓" in row and "Side —" in row and "End —" in row, f"3D part row: {row!r}"
    assert pg.is_visible("#cal-job-build"), "no Build 3D part chip in the job panel"
    assert pg.get_attribute('#cal-job-panel [data-shot="top"]', "data-have") == "1", "Top not ticked"
    assert pg.get_attribute('#cal-job-panel [data-shot="end"]', "data-have") == "0", "End ticked"
    shots = pg.inner_text("#cal-job-panel")
    assert "lay the ruler flat ON the face that photo shows" in shots, "Suggested-shots footer not updated"
    bg = pg.evaluate("getComputedStyle(document.getElementById('cal-job-btn')).backgroundColor")
    assert bg not in ("rgb(0, 212, 255)",), f"Submit to Datum still drawn in the accent fill: {bg}"
    pg.click("#cal-job-panel button[aria-label='Close']")

    pg.reload(wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
    pg.evaluate("window.CalibJob.ready")
    assert pg.evaluate("window.CalibJob.count()") == 1, "the photo did not survive the reload"
    pg.wait_for_selector("#cal-landing-job", timeout=8000)
    note = pg.inner_text("#cal-landing-job")
    assert "no photos saved" not in note and "1 photo" in note, f"landing: {note!r}"
    pg.evaluate("window.CalibJob.open()")
    pg.wait_for_selector("#cal-job-panel", timeout=5000)
    pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()
    pg.wait_for_function(READY + f"&&{OV}.ann.items.some(a=>a.type==='segment')", timeout=15000)
    same = pg.evaluate("window.CalibJob.part().views.top.source===window.CalibJob.photoId()")
    assert same, "the reopened photo is not the Top view's photo"

    # Removing the photo asks first (cancel keeps it), then flags the view it fed.
    pg.evaluate("window.CalibJob.open()")
    pg.wait_for_selector("#cal-job-panel", timeout=5000)
    before = len(dialogs)
    pg.locator("#cal-job-panel").get_by_role("button", name="Remove").first.click()
    assert len(dialogs) == before + 1 and "Top view" in dialogs[-1], f"no confirm: {dialogs[before:]}"
    assert pg.evaluate("window.CalibJob.count()") == 1, "cancel did not abort the removal"
    assert not pg.evaluate("!!window.CalibJob.part().views.top.photo_removed"), "flag set on cancel"
    dialogs.pop()
    pg.evaluate("window.confirm=()=>true")
    pg.locator("#cal-job-panel").get_by_role("button", name="Remove").first.click()
    gone = pg.evaluate("({n:window.CalibJob.count(), flag:window.CalibJob.part().views.top.photo_removed===true,"
                       "pts:window.CalibJob.part().views.top.points.length})")
    assert gone["n"] == 0 and gone["flag"] and gone["pts"] > 0, f"remove: {gone}"
    ctx.close()


def scenario_side_switch(browser, errors, dialogs):
    ctx, pg = _context(browser, errors, dialogs)
    with pg.expect_file_chooser() as fc:
        pg.click("#cal-landing-load button")
    fc.value.set_files(_bundle())
    pg.wait_for_selector("#cal-job-panel", timeout=8000)
    pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()
    pg.wait_for_function(READY + "&&document.querySelector('.cal-view').getAttribute('data-view-index')==='0'",
                         timeout=15000)
    _upload(pg, _photo("end", 80))
    # The photo on screen is listed, marked as not stored.
    pg.wait_for_selector("#cal-side-switcher #cal-side-unsaved", timeout=8000)
    # Outline, scale, End, and the tap on Top in one go — before autosave could store it — so
    # only the switcher's own capture can have kept it.
    pg.evaluate("(()=>{" + SCALE + ";" + OUTLINE_STMT +
                "window.CalibPart3D.assign('end');"
                "[...document.querySelectorAll('#cal-side-switcher button')].find(b=>b.textContent.indexOf('Top')===0).click();})()")
    pg.wait_for_function(READY + "&&document.querySelector('.cal-view').getAttribute('data-view-index')==='0'",
                         timeout=15000)
    src = pg.evaluate("window.CalibJob.part().views.end&&window.CalibJob.part().views.end.source")
    stored = pg.evaluate("(async()=>{await window.CalibJob.flush();const j=await window.CalibJob.persisted();"
                         "return j.views.map(v=>({label:v.label,sid:v.sourceId&&v.sourceId.slice(0,40)}));})()")
    assert len(stored) == 4, f"the fresh photo was dropped on the side switch: {stored}"
    assert stored[3]["label"] == "End", f"stored under {stored[3]['label']!r}, not its role"
    ok = pg.evaluate("(async()=>{const j=await window.CalibJob.persisted();const s=window.CalibJob.part().views.end.source;"
                     "return j.views.some(v=>v.sourceId===s||v.photoKey===s);})()")
    assert ok, f"End's source {src} resolves to no stored side"
    # The switcher tags the side with its role.
    pg.wait_for_function("[...document.querySelectorAll('#cal-side-switcher button')].some(b=>b.getAttribute('data-roles')==='end'"
                         "&&b.textContent.indexOf('🧩')>0)", timeout=5000)
    ctx.close()


def scenario_new_button(browser, errors, dialogs):
    ctx, pg = _context(browser, errors, dialogs)
    _upload(pg, _photo("new", 90))
    # An outline (no scale yet), then straight to New's save-before-reload.
    n = pg.evaluate("(async()=>{" + OUTLINE_STMT +
                    "await window.CalibJob.beforeReload();const j=await window.CalibJob.persisted();"
                    "return j?j.views.length:0;})()")
    assert n == 1, f"New dropped a photo with an outline on it (stored {n})"
    ctx.close()


def scenario_simple_card(browser, errors, dialogs):
    ctx, pg = _context(browser, errors, dialogs, simple=True, viewport={"width": 1366, "height": 768})
    _upload(pg, _photo("simple", 70))
    pg.wait_for_selector("#cal-simple", state="visible", timeout=8000)
    # Precondition: the card covers the ruler (this is the photo the bug needs).
    a = pg.evaluate(TO_SCREEN, [RULER_X[0], RULER_Y])
    assert not a["onCanvas"], f"precondition: the card should cover the ruler, top={a['top']}"
    # The top bar's Set scale chip, then the line tool from its chooser.
    pg.click("#cal-tb-scale")
    pg.get_by_text("Measure something of known size").click()
    pg.wait_for_selector("#cal-simple", state="detached", timeout=3000)
    a = pg.evaluate(TO_SCREEN, [RULER_X[0], RULER_Y])
    b = pg.evaluate(TO_SCREEN, [RULER_X[1], RULER_Y])
    assert a["onCanvas"] and b["onCanvas"], f"the ruler is still covered: {a['top']}, {b['top']}"
    pg.mouse.click(a["x"], a["y"])
    pg.mouse.click(b["x"], b["y"])
    pg.wait_for_selector("#cal-scale-input", state="visible", timeout=5000)
    pg.fill("#cal-scale-input input", "300")
    pg.locator("#cal-scale-input").get_by_role("button", name="Set").click()
    pg.wait_for_function(f"{OV}.isCalibrated()", timeout=5000)

    # Back to the simple card: the scale is worded by where it came from.
    pg.click("#cal-simple-toggle")
    pg.wait_for_selector("#cal-simple", state="visible", timeout=5000)
    st = pg.inner_text("#cal-simple .st")
    assert "Marker found" not in st, f"a ruler line reads as a marker: {st!r}"
    assert "Scale set from your ruler line (300 mm" in st and "mm/px" in st, f"line status: {st!r}"
    assert "Trace outline" in st, f"no next step after the scale: {st!r}"
    assert pg.get_attribute("#cal-simple-trace", "class") == "prim", "Trace is not the lead once scaled"
    labels = pg.evaluate("[...document.querySelectorAll('#cal-simple [data-role]')].map(b=>b.textContent)")
    assert labels == ["Top", "Side", "End"], f"role chips: {labels}"
    assert pg.is_visible("#cal-job-btn"), "the job button is hidden in simple mode"
    # Squared up: worded as tilt-corrected, with the angle.
    pg.evaluate(f"(()=>{{const o={OV};const H=window.CalibGeom.solveHomography("
                "[[300,1100],[900,1100],[900,1200],[300,1200]],[[0,0],[300,0],[300,50],[0,50]]);"
                "o.setManualRectScale(H,0.5,{kind:'squared',tilt_deg:4.6},600);})()")
    pg.wait_for_function("document.querySelector('#cal-simple .st').innerText.includes('Square up 4.6°')", timeout=3000)
    assert "Marker found" not in pg.inner_text("#cal-simple .st")

    # An outline selected, then the card's Top chip: the outline becomes the Top view, the
    # photo is kept as "Top", and Build leads with Review & Submit a ghost.
    pg.evaluate(OUTLINE)
    pg.wait_for_function("document.querySelector('#cal-simple .st').innerText.includes('Top, Side or End')", timeout=3000)
    pg.click('#cal-simple [data-role="top"]')
    pg.wait_for_function("!!window.CalibJob.part().views.top", timeout=3000)
    pg.wait_for_selector("#cal-simple-build", timeout=3000)
    assert pg.get_attribute("#cal-simple-build", "class") == "prim", "Build 3D part is not the lead"
    assert pg.get_attribute("#cal-simple-submit", "class") == "ghost", "Review & Submit still leads"
    assert pg.evaluate("window.CalibJob.count()") == 1, "the photo was not kept"
    assert pg.get_attribute('#cal-simple [data-role="top"]', "class") == "lbl on", "Top chip not marked"
    pg.wait_for_function("document.querySelector('#cal-simple').innerText.includes('Add another photo')", timeout=3000)

    # Phone width with the outline selected: the card folds to one row.
    pg.set_viewport_size({"width": 390, "height": 844})
    pg.wait_for_function("document.getElementById('cal-simple').classList.contains('mini')", timeout=3000)
    h = pg.evaluate("document.getElementById('cal-simple').getBoundingClientRect().height")
    assert h < 80, f"collapsed card is {h:.0f}px tall"
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
            scenario_fresh_photo(browser, errors, dialogs)
            scenario_side_switch(browser, errors, dialogs)
            scenario_new_button(browser, errors, dialogs)
            scenario_simple_card(browser, errors, dialogs)
            assert not errors, f"page errors: {errors}"
            assert not dialogs, f"unexpected dialogs: {dialogs}"
            browser.close()
        print("E2E_RESULT: PASS (fresh photo kept through capture/assign/reload with its measurement; "
              "alignment kept on re-assign; remove asks and flags; side switch keeps a fresh End photo; "
              "New keeps an outlined photo; simple-mode scale taps reach the ruler; card worded by source "
              "and leads to Build)")
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
