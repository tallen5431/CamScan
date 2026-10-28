"""Browser end-to-end test for the Build 3D part dialog's choices, checks and phone layout.

A few phone photos of a step-pad cover gave a Top and an End that disagreed on width by 6-12%,
an oblique photo saved as the Side, a scale cleared while views followed its outline, and a
679 mm part. This drives the dialog through each, in Chromium:

  1. Real server: an End 12% wider than the Top is refused; "Scale End to match Top" fixes it
     in place (the scale is stored on the view, and Undo puts it back). A 3 mm cover built
     with the box unticked never says it downloads upside down, and the box is relabelled.
     The End preset fills the 3D canvas. Known sizes and the end slope are sent; on a server
     that applies them, the length comes out at the typed 700 mm and the ends are reported.
  2. Stubbed server answers (so the client's side of the contract is pinned on its own): a fit
     shows both readings and "Use End" sends axis_source; a refusal with `detail` offers both
     Match buttons, and none past 30%; a 679 mm part needs 3 pieces on a 256 mm bed.
  3. A fresh photo: saving its outline stores the photo in the job; the picker gives a
     capture tip per role; one view opens the dialog on the missing size, and Escape closes
     it; a 160 x 134 mm outline saved as Side against a 679 mm Top is questioned before it
     is saved; clearing the photo's scale leaves the view's last scaled copy, flagged.
  4. A 390 x 844 phone: the downloads are on screen without scrolling, a vertical swipe on
     the 3D canvas scrolls the dialog (and does not turn the part), a sideways drag turns
     it, a double-tap resets it, and the dialog's targets are finger-sized.

Not part of the fast smoke test (it needs Chromium + a running server). Run it directly:

    .venv/bin/python tests/test_build_dialog_e2e.py

It SKIPS cleanly (exit 0) where Playwright or a browser isn't available, so it never breaks
an environment that can't run a browser — unless CAMSCAN_E2E_REQUIRE=1, as CI sets it.
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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8206")


def _skip(msg):
    # A skip is a pass by default, so a machine with no browser is never broken by this.
    # CI sets CAMSCAN_E2E_REQUIRE=1: there a skip means the browser layer went untested.
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
OV = "document.querySelector('.cal-view').__overlay"


def _rect_view(w_mm, h_mm, source):
    """A stored part view: a w x h mm rectangle traced at MM_PER_PX on a 600 px tall photo."""
    w, h = w_mm / MM_PER_PX, h_mm / MM_PER_PX
    return {"points": [[100, 100], [100 + w, 100], [100 + w, 100 + h], [100, 100 + h]],
            "mm_per_px": MM_PER_PX, "image_height": 600, "size_mm": [w_mm, h_mm],
            "source": source, "ann_id": None, "rot": 0, "flip_h": False, "flip_v": False}


SET_VIEWS = """(v)=>{const p=window.CalibJob.part(); p.views={}; p.opts={};
  Object.assign(p.views, v); window.CalibJob.savePart(); return Object.keys(p.views).length;}"""


def _photo():
    """A fresh upload with no calibration target in it (the plain photo test_part3d_e2e uses
    for "no scale yet"): its outline is drawn by hand below, as 320 x 266 px points."""
    img = np.full((600, 800, 3), 190, np.uint8)
    cv2.rectangle(img, (250, 200), (550, 400), (70, 70, 70), -1)
    path = os.path.join(tempfile.gettempdir(), "build-dialog-e2e.jpg")
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


def _status(pg):
    return pg.inner_text("#cal-part-status")


def _canvas_fill(pg):
    """The drawn part's bounding box in the 3D canvas, as fractions of its width and height."""
    return pg.evaluate("""(()=>{const c=document.getElementById('cal-part-3d'), g=c.getContext('2d');
      const d=g.getImageData(0,0,c.width,c.height).data; let x0=1e9,y0=1e9,x1=-1,y1=-1;
      for(let y=0;y<c.height;y++) for(let x=0;x<c.width;x++){ const i=(y*c.width+x)*4;
        if(Math.abs(d[i]-11)+Math.abs(d[i+1]-16)+Math.abs(d[i+2]-19)>30){
          if(x<x0)x0=x; if(x>x1)x1=x; if(y<y0)y0=y; if(y>y1)y1=y; } }
      return x1<0 ? null : {w:(x1-x0+1)/c.width, h:(y1-y0+1)/c.height,
                           min:Math.max(x1-x0+1,y1-y0+1)/Math.min(c.width,c.height)};})()""")


def _box_mesh(x, y, z):
    v = [[0, 0, 0], [x, 0, 0], [x, y, 0], [0, y, 0], [0, 0, z], [x, 0, z], [x, y, z], [0, y, z]]
    f = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
         [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
    return {"v": v, "f": f}


def _real_server(pg, notes):
    # 1a. An End 12% wider than the Top: refused, with both ways to fix it in place.
    pg.evaluate(SET_VIEWS, {"top": _rect_view(200, 90, "photo-top"), "end": _rect_view(100.8, 25, "photo-end")})
    with pg.expect_response(lambda r: "/api/export/part" in r.url, timeout=20000) as first:
        pg.evaluate("window.CalibPart3D.open()")
    assert first.value.status == 422, f"a 12% mismatch was not refused: {first.value.status}"
    pg.wait_for_selector('#cal-part-status button[data-match="end"]', timeout=8000)
    st = _status(pg)
    assert "Scale End to match Top ×0.893" in st and "Scale Top to match End ×1.120" in st, f"match buttons: {st!r}"
    assert pg.is_disabled("#cal-part-build"), "download enabled on a refused build"
    #    STEP and OBJ are off too, and all three look it (the disabled STL kept opacity 1).
    assert pg.is_disabled("#cal-part-build-step") and pg.is_disabled("#cal-part-build-obj"), "STEP/OBJ live on a refusal"
    assert pg.evaluate("getComputedStyle(document.getElementById('cal-part-build')).opacity") == "0.45"
    with pg.expect_response(lambda r: "/api/export/part" in r.url, timeout=20000) as fixed:
        pg.click('#cal-part-status button[data-match="end"]')
    fj = fixed.value.json()
    assert fixed.value.status == 200 and fj.get("ok"), f"matched preview refused: {fj}"
    sc = pg.evaluate("window.CalibJob.part().views.end.scale")
    assert sc and abs(sc - 90 / 100.8) < 1e-3, f"scale not stored on the End view: {sc}"
    assert abs(fj["info"]["size_mm"][1] - 90) < 0.5, f"width after match: {fj['info']['size_mm']}"
    pg.wait_for_function("document.getElementById('cal-part-row-end').innerText.includes('scaled ×0.893')", timeout=5000)
    assert not pg.is_disabled("#cal-part-build"), "download still disabled after the match"
    #    Undo puts the old scale back, and the build is refused again.
    with pg.expect_response(lambda r: "/api/export/part" in r.url, timeout=20000) as undone:
        pg.click('#cal-part-row-end button[data-act="unscale"]')
    assert undone.value.status == 422, "Undo did not restore the mismatch"
    assert pg.evaluate("window.CalibJob.part().views.end.scale") is None, "Undo left a scale behind"
    pg.click("#cal-part-panel button[aria-label='Close']")

    # 1b. Top + End, a 3 mm wall, the box unticked: plate-down, never "upside down".
    pg.evaluate(SET_VIEWS, {"top": _rect_view(200, 90, "photo-top"), "end": _rect_view(90, 25, "photo-end")})
    with pg.expect_response(lambda r: "/api/export/part" in r.url, timeout=20000):
        pg.evaluate("window.CalibPart3D.open()")
    assert pg.inner_text('label:has(#cal-part-upside)').strip() == "Upside down for printing"
    with pg.expect_response(lambda r: "/api/export/part" in r.url and r.request.post_data
                            and '"wall_mm":3' in r.request.post_data, timeout=20000) as walled:
        pg.fill("#cal-part-wall", "3")
    wj = walled.value.json()
    assert wj.get("ok"), f"3 mm cover refused: {wj}"
    pg.wait_for_function("document.getElementById('cal-part-status').innerText.includes('Part:')", timeout=8000)
    st = _status(pg)
    assert "upside down" not in st.lower(), f"unticked cover says upside down: {st!r}"
    assert "plate-down" in st, f"orientation not said: {st!r}"
    assert pg.inner_text('label:has(#cal-part-upside)').strip() == "Turn over: open side down (needs supports)"

    # 1c. The End preset fills the canvas, where the bounding sphere left a speck.
    pg.wait_for_function("+document.getElementById('cal-part-3d').getAttribute('data-faces')>0", timeout=8000)
    pg.click('[data-view3d="end"]')
    fill = _canvas_fill(pg)
    assert fill and fill["min"] >= 0.6, f"End view fills too little of the canvas: {fill}"
    # Turned sideways by hand from End, the 679 mm length still fits, and a nudge does not jump.
    cb = pg.locator("#cal-part-3d").bounding_box()
    cx, cy = cb["x"] + cb["width"] / 2, cb["y"] + cb["height"] / 2
    pg.mouse.move(cx, cy)
    pg.mouse.down()
    pg.mouse.move(cx + 2, cy)
    nudge = _canvas_fill(pg)
    assert nudge and abs(nudge["min"] - fill["min"]) < 0.05, f"the part jumped when turned: {fill} -> {nudge}"
    pg.mouse.move(cx + 120, cy, steps=6)
    pg.mouse.up()
    side = _canvas_fill(pg)
    assert side and side["w"] < 0.99 and side["h"] < 0.99, f"turned from End, the part overflows: {side}"
    pg.click('[data-view3d="iso"]')

    # 1d. A known length and an end slope go to the server; one that applies them says so.
    #     The cover's own sizes: a 679 mm Top whose length a tape says is 700.
    pg.click("#cal-part-panel button[aria-label='Close']")
    pg.evaluate(SET_VIEWS, {"top": _rect_view(679, 164.6, "photo-top"), "end": _rect_view(164.6, 60, "photo-end")})
    with pg.expect_response(lambda r: "/api/export/part" in r.url, timeout=20000):
        pg.evaluate("window.CalibPart3D.open()")
    with pg.expect_request(lambda q: "/api/export/part" in q.url and q.post_data
                           and '"known_mm"' in q.post_data, timeout=20000) as kq:
        pg.fill("#cal-part-known-length", "700")
    body = json.loads(kq.value.post_data)
    assert body["known_mm"] == {"length": 700}, f"known_mm: {body.get('known_mm')}"
    kj = kq.value.response().json()
    if kj.get("ok") and "deskew" in kj.get("info", {}):
        assert abs(kj["info"]["size_mm"][0] - 700) < 0.5, f"typed 700, got {kj['info']['size_mm']}"
        notes.append("known length applied (700 mm)")
    else:
        notes.append("known length sent (this server does not apply it)")
    pg.fill("#cal-part-known-length", "")
    assert pg.is_visible("#cal-part-ends"), "end shape fields hidden with no Side view"
    shot = pg.evaluate("document.getElementById('cal-part-3d').toDataURL()")
    with pg.expect_request(lambda q: "/api/export/part" in q.url and q.post_data
                           and '"ends"' in q.post_data, timeout=20000) as eq:
        pg.fill("#cal-part-end-slope", "30")
    body = json.loads(eq.value.post_data)
    assert body["ends"] == {"slope_deg": 30, "radius_mm": 0}, f"ends: {body.get('ends')}"
    ej = eq.value.response().json()
    if ej.get("ok") and ej.get("info", {}).get("ends"):
        pg.wait_for_function("document.getElementById('cal-part-status').innerText.includes('Ends: 30° slope')",
                             timeout=8000)
        pg.wait_for_function(f"document.getElementById('cal-part-3d').toDataURL() !== {json.dumps(shot)}",
                             timeout=8000)
        notes.append("end slope applied")
    else:
        notes.append("end slope sent (this server does not apply it)")
    pg.click("#cal-part-panel button[aria-label='Close']")


def _stubbed(pg):
    bodies, state = [], {}

    def handle(route):
        bodies.append(json.loads(route.request.post_data or "{}"))
        status, payload = state["resp"]
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

    pg.route("**/api/export/part", handle)
    try:
        # 2a. Both readings, who set the size, a deskew, the orientation, and the bed.
        state["resp"] = (200, {"ok": True, "preview": {"views": {}, "size_mm": [679, 164.6, 79.7],
                                                       "mesh": _box_mesh(679, 164.6, 79.7)},
                               "info": {"size_mm": [679, 164.6, 79.7], "kind": "shell", "warnings": [],
                                        "fits": [{"view": "end", "axis": "width", "pct": 6.7, "have_mm": 175.7,
                                                  "want_mm": 164.6, "setter": "top", "kind": "rescale"}],
                                        "deskew": [{"view": "top", "deg": 1.1}], "upside_down": False,
                                        "plate_down": True,
                                        "orientation": "plate-down, open side up — ready to print (no supports)"}})
        pg.evaluate("localStorage.removeItem('camscan.printBed')")
        pg.evaluate(SET_VIEWS, {"top": _rect_view(679, 164.6, "photo-top"), "end": _rect_view(175.7, 80, "photo-end")})
        pg.evaluate("window.CalibPart3D.open()")
        pg.wait_for_function("document.getElementById('cal-part-status').innerText.includes('Width:')", timeout=8000)
        st = _status(pg)
        for want in ("Width: Top 164.6 mm, End 175.7 mm (6.7% apart) — using Top", "Use End",
                     "Top view turned 1.1° to square it.",
                     "Downloads plate-down, open side up — ready to print (no supports).",
                     "needs about 3 pieces along its length (cuts near 226 / 453 mm)"):
            assert want in st, f"missing {want!r} in {st!r}"
        assert "upside down" not in st.lower(), st
        n = len(bodies)
        pg.click('#cal-part-status button[data-use-axis="width"]')
        deadline = time.time() + 5
        while len(bodies) <= n and time.time() < deadline:
            pg.wait_for_timeout(100)
        assert len(bodies) > n and bodies[-1].get("axis_source") == {"width": "end"}, \
            f"Use End did not send axis_source: {bodies[-1] if bodies else None}"
        assert pg.evaluate("window.CalibJob.part().opts.axis_source.width") == "end"
        #    The bed is the viewer's: a 700 mm bed fits it in one piece.
        pg.fill("#cal-part-bed-x", "700")
        pg.wait_for_function("!document.getElementById('cal-part-status').innerText.includes('pieces')", timeout=5000)
        assert pg.evaluate("localStorage.getItem('camscan.printBed')") == "[700,256]"
        pg.fill("#cal-part-bed-x", "256")
        pg.click("#cal-part-panel button[aria-label='Close']")

        # 2b. A refusal that carries `detail`: both Match buttons, from the data, not the text.
        state["resp"] = (422, {"ok": False, "error": "These two views disagree.",
                               "detail": {"view": "end", "axis": "width", "have_mm": 177.8, "want_mm": 158.7,
                                          "setter": "top"}})
        pg.evaluate(SET_VIEWS, {"top": _rect_view(158.7, 600, "photo-top"), "end": _rect_view(177.8, 60, "photo-end")})
        pg.evaluate("window.CalibPart3D.open()")
        pg.wait_for_selector('#cal-part-status button[data-match="top"]', timeout=8000)
        n = len(bodies)
        pg.click('#cal-part-status button[data-match="top"]')
        deadline = time.time() + 5
        while len(bodies) <= n and time.time() < deadline:
            pg.wait_for_timeout(100)
        sc = pg.evaluate("window.CalibJob.part().views.top.scale")
        assert sc and abs(sc - 177.8 / 158.7) < 1e-3, f"Top scale: {sc}"
        sent = bodies[-1]["views"]["top"]["mm_per_px"]
        assert abs(sent - MM_PER_PX * 177.8 / 158.7) < 1e-4, f"matched Top sent at {sent} mm/px"
        pg.click("#cal-part-panel button[aria-label='Close']")

        # 2c. Past 30% the likelier cause is the wrong photo: no Match buttons.
        state["resp"] = (422, {"ok": False, "error": "The end view's width is 35% longer than the top view's "
                                                     "(214.3 vs 158.7 mm).",
                               "detail": {"view": "end", "axis": "width", "have_mm": 214.3, "want_mm": 158.7,
                                          "setter": "top"}})
        pg.evaluate("window.CalibPart3D.open()")
        pg.wait_for_function("document.getElementById('cal-part-status').innerText.includes('35% longer')", timeout=8000)
        assert pg.locator("#cal-part-status button[data-match]").count() == 0, "Match offered at 35%"
        pg.click("#cal-part-panel button[aria-label='Close']")
    finally:
        pg.unroute("**/api/export/part")


def _fresh_photo(pg):
    pg.evaluate(SET_VIEWS, {})
    pg.set_input_files("#uploader input[type=file]", _photo())
    pg.wait_for_function("(()=>{var v=document.querySelector('.cal-view');"
                         "return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()", timeout=20000)
    # The Set scale chooser opens by itself on a photo with no scale; close it.
    try:
        pg.wait_for_selector(".cal-coach", state="visible", timeout=8000)
        pg.locator(".cal-coach").click(position={"x": 5, "y": 5})
        pg.wait_for_selector(".cal-coach", state="detached", timeout=5000)
    except Exception:
        pass
    assert not pg.evaluate(f"{OV}.isCalibrated()"), "the plain photo found a scale of its own"
    pg.evaluate(f"{OV}.setManualScaleFromPixels(200, 100)")            # 0.5 mm/px
    # A closed outline placed dot by dot, as Outline mode does: 320 x 266 px = 160 x 133 mm.
    pg.evaluate(f"""(()=>{{const o={OV}; o.setMode('outline');
      o.selectedPoints=[[240,167],[560,167],[560,433],[240,433]]; o.finishPolyline();
      const pl=o.ann.items.find(a=>a.type==='polyline'&&a.closed); o.ann.selectedId=pl.id; o.setMode('select'); o.requestDraw();}})()""")
    pg.wait_for_function(f"{OV}.ann.items.some(a=>a.type==='polyline'&&a.closed)", timeout=5000)
    assert pg.evaluate("window.CalibJob.count()") == 0, "photo stored before any outline was saved"

    # 3a. The picker says how to take each view's photo; saving stores the photo.
    pg.locator("#cal-ctx-3d").click()
    assert pg.locator("#cal-part-pick .cal-part-tip").count() == 4, "no capture tip per role"
    pg.locator('#cal-part-pick [data-role="top"]').click()
    pg.wait_for_function("!!window.CalibJob.part().views.top", timeout=5000)
    assert pg.evaluate("window.CalibJob.count()") == 1, "the outline's photo was not stored in the job"
    toast = pg.evaluate("window.__calLastToast && window.__calLastToast.innerText") or ""
    assert "(160.0 × 133.0 mm)" in toast, f"size not larger × smaller: {toast!r}"
    assert "Add a Side or End photo for the height" in toast, f"toast does not say what is missing: {toast!r}"

    # 3b. One view: the dialog opens on the size it lacks, and Escape closes it at once.
    pg.evaluate("window.CalibPart3D.open()")
    pg.wait_for_selector("#cal-part-onenote", timeout=5000)
    note = pg.locator("#cal-part-onenote").bounding_box()
    dep = pg.locator("#cal-part-depth").bounding_box()
    assert note["y"] + note["height"] <= dep["y"], "the one-view note is not above the depth field"
    assert pg.evaluate("document.activeElement && document.activeElement.id") == "cal-part-depth", "depth not focused"
    assert "Height / thickness if no view shows it" in pg.inner_text("label:has(#cal-part-depth)")
    assert "⟲" not in pg.inner_text("#cal-part-intro"), "align hint shown with one view"
    pg.keyboard.press("Escape")
    pg.wait_for_selector("#cal-part-panel", state="detached", timeout=3000)

    # 3c. The same outline saved as Side against a 679 mm Top: asked before it is saved.
    pg.evaluate("(v)=>{const p=window.CalibJob.part(); p.views={top:v}; window.CalibJob.savePart();}",
                _rect_view(679, 164.6, "another-photo"))
    pg.locator("#cal-ctx-3d").click()
    pg.locator('#cal-part-pick [data-role="side"]').click()
    pg.wait_for_function("window.__calLastToast && window.__calLastToast.innerText.includes('Save anyway')", timeout=5000)
    warn = pg.evaluate("window.__calLastToast.innerText")
    assert "76% shorter than the Top view" in warn and "End view" in warn, f"warning: {warn!r}"
    assert pg.evaluate("!window.CalibJob.part().views.side"), "saved before the user answered"
    # Save anyway saves the outline the warning was about, even once the selection has moved.
    pg.evaluate(f"(()=>{{const o={OV}; o.ann.selectedId=null; o.requestDraw();}})()")
    pg.locator(".cal-toast-action", has_text="Save anyway").click()
    pg.wait_for_function("!!window.CalibJob.part().views.side", timeout=5000)
    sz = pg.evaluate("window.CalibJob.part().views.side.size_mm")
    assert sorted(round(x) for x in sz) == [133, 160], f"Save anyway saved another outline: {sz}"

    # 3c'. Traced again on a photo already matched: the new outline takes the photo's factor,
    #      as its cavity kept it.
    pg.evaluate(f"(()=>{{const o={OV}, pl=o.ann.items.find(a=>a.type==='polyline'&&a.closed);"
                " o.ann.selectedId=pl.id; o.requestDraw();"
                " const p=window.CalibJob.part(), s=p.views.side;"
                " p.views.cavity=Object.assign({}, s, {scale:0.9, scale_undo:1, scale_note:'matched to Top'});"
                " window.CalibJob.savePart();})()")
    pg.evaluate("window.CalibPart3D.assign('side', true)")
    side = pg.evaluate("window.CalibJob.part().views.side")
    assert side.get("scale") == 0.9 and side.get("scale_note") == "matched to Top", f"factor not carried: {side}"
    pg.evaluate("(()=>{const p=window.CalibJob.part(); delete p.views.cavity; delete p.views.side.scale;"
                " delete p.views.side.scale_undo; delete p.views.side.scale_note; window.CalibJob.savePart();})()")

    # 3d. Clearing the photo's scale leaves the view's last scaled copy, flagged.
    mpp = pg.evaluate("window.CalibJob.part().views.side.mm_per_px")
    pg.evaluate(f"{OV}.clearManualScale()")
    pg.wait_for_timeout(2000)
    pg.evaluate("window.CalibPart3D.sync()")
    side = pg.evaluate("window.CalibJob.part().views.side")
    assert abs(side["mm_per_px"] - mpp) < 1e-9, f"unscaled photo overwrote the view: {mpp} -> {side['mm_per_px']}"
    assert side.get("unscaled"), "unscaled view not flagged"
    pg.evaluate("window.CalibPart3D.open()")
    row = pg.inner_text("#cal-part-row-side")
    assert "no scale right now" in row and "follows its outline" not in row, f"side row: {row!r}"
    pg.keyboard.press("Escape")


def _phone(browser, notes):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && window.CalibPart3D", timeout=15000)
    pg.evaluate(SET_VIEWS, {"top": _rect_view(200, 90, "photo-top"), "end": _rect_view(90, 25, "photo-end")})
    with pg.expect_response(lambda r: "/api/export/part" in r.url, timeout=20000):
        pg.evaluate("window.CalibPart3D.open()")
    pg.wait_for_function("+document.getElementById('cal-part-3d').getAttribute('data-faces')>0", timeout=8000)
    iso = pg.evaluate("document.getElementById('cal-part-3d').toDataURL()")

    # 4a. The downloads are on the first screen.
    assert pg.evaluate("document.getElementById('cal-part-panel').scrollTop") == 0
    for sel in ("#cal-part-build", "#cal-part-status"):
        b = pg.locator(sel).bounding_box()
        assert b["y"] >= 0 and b["y"] + min(b["height"], 46) <= 844, f"{sel} off screen: {b}"
    assert pg.inner_text("#cal-part-3d-hint") == "drag sideways to turn · pinch to zoom · double-tap to reset"
    assert pg.evaluate("getComputedStyle(document.getElementById('cal-part-3d')).touchAction") == "pan-y"

    # 4b. Finger-sized targets.
    small = pg.evaluate("""(()=>{const bad=[]; const r=e=>e.getBoundingClientRect();
      document.querySelectorAll('#cal-part-card label:has(input[type=checkbox])').forEach(e=>{ if(r(e).height<44) bad.push('check '+r(e).height); });
      document.querySelectorAll('[data-view3d]').forEach(e=>{ if(r(e).height<40) bad.push('chip '+r(e).height); });
      document.querySelectorAll('#cal-part-card [id^=cal-part-row-] button').forEach(e=>{ if(r(e).height<44||r(e).width<44) bad.push('row '+r(e).width+'x'+r(e).height); });
      const x=document.querySelector("#cal-part-panel button[aria-label='Close']"); if(r(x).height<44||r(x).width<44) bad.push('close');
      return bad;})()""")
    assert not small, f"targets under size: {small}"

    # 4c. A vertical swipe on the canvas scrolls the dialog and leaves the part as it was.
    pg.evaluate("""(()=>{const p=document.getElementById('cal-part-panel'), c=document.getElementById('cal-part-3d');
      p.scrollTop += c.getBoundingClientRect().top - 80;})()""")
    s0 = pg.evaluate("document.getElementById('cal-part-panel').scrollTop")
    cb = pg.locator("#cal-part-3d").bounding_box()
    cx, cy = cb["x"] + cb["width"] / 2, cb["y"] + 40
    cdp = ctx.new_cdp_session(pg)

    def touch(kind, x, y):
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [] if kind == "touchEnd" else
                                              [{"x": x, "y": y, "id": 1}]})
    touch("touchStart", cx, cy + 100)
    for k in range(1, 11):
        touch("touchMove", cx, cy + 100 - 22 * k)
        pg.wait_for_timeout(16)
    touch("touchEnd", 0, 0)
    pg.wait_for_timeout(400)
    s1 = pg.evaluate("document.getElementById('cal-part-panel').scrollTop")
    assert s1 > s0 + 50, f"vertical swipe on the canvas did not scroll the dialog: {s0} -> {s1}"
    assert pg.evaluate("document.getElementById('cal-part-3d').toDataURL()") == iso, "vertical swipe turned the part"

    # 4d. A sideways one-finger drag turns it; a double-tap puts it back.
    pg.evaluate("""(()=>{const p=document.getElementById('cal-part-panel'), c=document.getElementById('cal-part-3d');
      p.scrollTop += c.getBoundingClientRect().top - 80;})()""")
    pg.wait_for_timeout(100)
    cb = pg.locator("#cal-part-3d").bounding_box()
    cx, cy = cb["x"] + cb["width"] / 2, cb["y"] + cb["height"] / 2
    touch("touchStart", cx - 60, cy)
    for k in range(1, 7):
        touch("touchMove", cx - 60 + 20 * k, cy)
        pg.wait_for_timeout(16)
    touch("touchEnd", 0, 0)
    pg.wait_for_timeout(100)
    assert pg.evaluate("document.getElementById('cal-part-3d').toDataURL()") != iso, "sideways drag did not turn the part"
    for _ in range(2):
        touch("touchStart", cx, cy)
        touch("touchEnd", 0, 0)
        pg.wait_for_timeout(60)
    pg.wait_for_timeout(100)
    assert pg.evaluate("document.getElementById('cal-part-3d').toDataURL()") == iso, "double-tap did not reset the view"
    assert not errors, f"phone page errors: {errors}"
    ctx.close()


def main():
    env = dict(os.environ, PORT=PORT, HOST="127.0.0.1")
    srv = subprocess.Popen([sys.executable, "app.py"], cwd=str(ROOT), env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    notes = []
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
            pg = ctx.new_page()
            errors, dialogs = [], []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && window.CalibPart3D && document.querySelector('#uploader input[type=file]')",
                                 timeout=15000)
            pg.evaluate("window.CalibJob.clearStored()")
            _real_server(pg, notes)
            _stubbed(pg)
            _fresh_photo(pg)
            assert not errors, f"page errors: {errors}"
            assert not dialogs, f"unexpected alerts: {dialogs}"
            ctx.close()
            _phone(browser, notes)
            browser.close()
        print("E2E_RESULT: PASS (12% End/Top mismatch matched in place and undone; 3 mm cover plate-down, "
              "relabelled; End preset fills the canvas; fit readings, Use End, Match from detail, none past 30%, "
              "bed pieces; photo stored on assign, capture tips, one-view note + Escape, wrong-view warning "
              "before saving, unscaled guard; phone: downloads on screen, swipe scrolls, drag turns, double-tap "
              "resets, targets finger-sized; " + "; ".join(notes) + ")")
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
