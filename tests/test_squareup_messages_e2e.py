"""Browser end-to-end for Square up's messages and the plane offset under a ruler line.

Boots the app, loads a photo with a ruler-line scale and an outline, and answers
/api/square_up with stubbed results to check what the page says and offers:
  * no ruler edges found: the reason, what using the part's sides assumes, how to get the
    ruler used — a "Redraw scale line" button (undoes, then Set scale) and "Use the ruler"
    shown greyed out rather than hidden;
  * one ruler edge only (partial): says the other was assumed parallel, offers the part's
    sides and Redraw;
  * nothing selected: the message says Square up uses the ruler under the scale line.
Then "Measuring a raised face?" on a ruler line: a face 45 mm below a ruler read at
0.516 mm/px, with no lens data (f = 1186 px for a 1200 x 1600 photo), reads 7.4% small, so
the scale grows by that and the message says it is an estimate. The offset survives a save
and reload without compounding, a new line clears it, and Square up over it says it is off.

Not part of the fast smoke test (needs Chromium + a running server). Run directly:
    .venv/bin/python tests/test_squareup_messages_e2e.py
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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8203")


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

W, H = 1200, 1600                  # a phone's portrait photo: the assumed lens is f = 1186 px


def _bundle_file():
    # A dark cover with a bright ruler down it, on light ground.
    img = np.full((H, W, 3), 175, np.uint8)
    cv2.rectangle(img, (300, 200), (900, 1400), (60, 62, 66), -1)
    cv2.rectangle(img, (570, 400), (630, 1000), (210, 210, 210), -1)
    _ok, enc = cv2.imencode(".jpg", img)
    raw = "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode()
    bundle = {
        "kind": "camscan.job", "version": 1, "id": "squareup-msg-e2e", "createdAt": "2026-01-01T00:00:00Z",
        "brief": {"part": "step pad cover"},
        "views": [{
            "label": "Top", "image": raw, "units": "mm",
            "scale": {"calibrated": True, "source": "manual line"}, "measurements": [],
            "restore": {"v": 1, "w": W, "h": H, "raw": raw,
                        "calib": {"markers": [], "image_size": {"width": W, "height": H}, "marker_size_mm": None},
                        "ann": [], "units": "mm", "manual": {"mmPerPx": 0.5, "homography": None, "ref": None}},
        }],
    }
    path = os.path.join(tempfile.gettempdir(), "squareup-msg-e2e.camscan.json")
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


O = "document.querySelector('.cal-view').__overlay"
SIDES = [[[300, 200], [300, 1400]], [[900, 200], [900, 1400]]]
H_SQ = [[0.5, 0.0, 0.0], [0.0, 0.5, 0.0], [0.0, 0.0, 1.0]]


def _result(**over):
    r = {"ok": True, "H": H_SQ, "reference": "part", "other": None, "sides": SIDES, "ruler_edges": None,
         "tilt_deg": 3.1, "width_change_pct": 5.2, "focal_source": "exif", "focal_px": 1186.0, "converge_deg": 0.3,
         "part": {"angle_deg": 0.0, "width_mm": [300.0, 300.0], "width_change_pct": 0.0},
         "ruler_check": None, "partial": False, "ruler_reason": None, "ruler_found": False}
    r.update(over)
    return r


def _buttons(pg):
    return pg.evaluate("[...document.querySelectorAll('.cal-toast-action')].map(b=>({t:b.textContent,d:b.disabled,title:b.title}))")


def main():
    bundle_path = _bundle_file()
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
            pg = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            pg.on("pageerror", lambda e: errors.append(str(e)))

            # The server's answer, set per step.
            reply = {"body": _result()}
            pg.route("**/api/square_up", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(reply["body"])))

            pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
            with pg.expect_file_chooser() as fc:
                pg.click("#cal-landing-load button")
            fc.value.set_files(bundle_path)
            pg.wait_for_selector("#cal-job-panel", timeout=8000)
            pg.locator("#cal-job-panel").get_by_role("button", name="Open").first.click()
            pg.wait_for_function(
                "(()=>{var v=document.querySelector('.cal-view');return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()",
                timeout=15000)

            # Nothing selected yet: the message says what Square up uses.
            pg.evaluate(f"(()=>{{const o={O};o.ann.selectedId=null;o.squareUp();}})()")
            pg.wait_for_function("document.body.innerText.includes('it uses the ruler under your scale line')", timeout=5000)

            # A 300 mm scale line down the ruler (581.4 px: 0.516 mm/px) and the cover's outline.
            oid = pg.evaluate(f"""(()=>{{const o={O};
                o.setManualScaleFromPixels(581.4, 300, [[600,409.3],[600,990.7]]);
                o._pushHistory();
                window.CalibAnn.addPolyline(o.ann,[[300,200],[900,200],[900,1400],[300,1400]],o.getScale(),o.opts.units,null,true);
                o.setMode('select');o.requestDraw();return o.ann.selectedId;}})()""")

            # 1) No ruler edges found along the line.
            reply["body"] = _result(ruler_reason="left edge unclear", focal_source="assumed")
            ok = pg.evaluate(f"{O}.squareUp()")
            assert ok, "square up with a stubbed result did not apply"
            pg.wait_for_function("document.body.innerText.includes('Redraw scale line')", timeout=5000)
            text = pg.evaluate("window.__calLastToast.innerText")
            for want in ("Couldn't find the ruler's edges along your scale line (left edge unclear)",
                         "assumes the part is a true rectangle",
                         "redraw the scale line down the ruler's middle, between two numbered marks far apart",
                         "no camera data"):
                assert want in text, f"missing {want!r} in {text!r}"
            btns = {b["t"]: b for b in _buttons(pg)}
            assert set(btns) >= {"Redraw scale line", "Use the ruler", "Undo"}, f"buttons: {list(btns)}"
            assert btns["Use the ruler"]["d"] and "left edge unclear" in btns["Use the ruler"]["title"], \
                f"'Use the ruler' should be shown disabled with the reason: {btns['Use the ruler']}"
            assert not btns["Redraw scale line"]["d"], "Redraw scale line is disabled"
            assert pg.evaluate(f"{O}._manualRef && {O}._manualRef.kind==='squared'"), "the result was not applied"
            # Redraw: undoes the Square up (the plain line scale is back) and starts Set scale.
            pg.locator(".cal-toast-action", has_text="Redraw scale line").click()
            pg.wait_for_function(f"{O}.opts.mode==='setscale' && !{O}.opts.manualHomography && {O}.opts.manualMmPerPx>0",
                                 timeout=5000)
            pg.wait_for_function("document.body.innerText.includes('Tap two numbered marks far apart')", timeout=5000)

            # 1b) Edges found, but they would not square up: not called "couldn't find".
            pg.evaluate(f"(()=>{{const o={O};o.setMode('select');o.ann.selectedId={oid};}})()")
            reply["body"] = _result(ruler_reason="the ruler's edges cross in the photo", ruler_found=True)
            assert pg.evaluate(f"{O}.squareUp()")
            text = pg.evaluate("window.__calLastToast.innerText")
            assert "Found the ruler's edges along your scale line but couldn't square up from them" in text \
                and "Couldn't find" not in text, text
            pg.locator(".cal-toast-action", has_text="Undo").click()
            pg.wait_for_function(f"!{O}.opts.manualHomography", timeout=5000)

            # 2) One ruler edge only: said plainly, the part's sides and Redraw on offer.
            pg.evaluate(f"(()=>{{const o={O};o.setMode('select');o.ann.selectedId={oid};}})()")
            reply["body"] = _result(reference="ruler", other="part", partial=True, ruler_reason="right edge unclear",
                                    ruler_edges=[[[570, 410], [570, 990]], [[630, 410], [630, 990]]],
                                    part={"angle_deg": 0.0, "width_mm": [300.0, 304.0], "width_change_pct": 1.3,
                                          "taper_note": "one ruler edge assumed parallel"})
            assert pg.evaluate(f"{O}.squareUp()"), "partial result did not apply"
            pg.wait_for_function("document.body.innerText.includes('only one of its edges was clear')", timeout=5000)
            text = pg.evaluate("window.__calLastToast.innerText")
            assert "right edge unclear" in text and "assumed parallel" in text and "not checked against the ruler" in text, text
            assert "NOT parallel" not in text, f"an unchecked taper was called one: {text!r}"
            names = [b["t"] for b in _buttons(pg)]
            assert "Use the part's sides instead" in names and "Redraw scale line" in names and "Undo" in names, names
            pg.locator(".cal-toast-action", has_text="Undo").click()
            pg.wait_for_function(f"!{O}.opts.manualHomography && {O}.opts.manualMmPerPx>0", timeout=5000)

            # 3) A refusal that names why the ruler was not found offers Redraw too.
            reply["body"] = {"ok": False, "error": "Could not find the ruler's edges along the scale line "
                                                   "(no ruler strip along the scale line) — draw it down the middle.",
                             "partial": False, "ruler_reason": "no ruler strip along the scale line"}
            assert pg.evaluate(f"{O}.squareUp('ruler')") is False
            pg.wait_for_function("document.body.innerText.includes('no ruler strip along the scale line')"
                                 " && [...document.querySelectorAll('.cal-toast-action')].some(b=>b.textContent==='Redraw scale line')",
                                 timeout=5000)

            # 4) The plane below the ruler. No lens data: f from the default field of view.
            base = pg.evaluate(f"{O}.opts.manualMmPerPx")
            assert abs(base - 0.516) < 0.001, base
            pg.evaluate(f"(()=>{{const o={O};o.data.camera={{focal_px:null,focal_source:'assumed'}};}})()")
            assert pg.evaluate(f"{O}.setFeatureHeight(-45)") is True, "a line scale refused the plane offset"
            eff = pg.evaluate(f"{O}.opts.manualMmPerPx")
            pct = 100 * (eff / base - 1)
            assert abs(pct - 7.4) < 0.15, f"expected about +7.4%, got {pct:.2f}%"
            pg.wait_for_function("document.body.innerText.includes('+7.4%')", timeout=5000)
            text = pg.evaluate("window.__calLastToast.innerText")
            assert "estimated (no lens data)" in text and "45 mm below the ruler" in text, text
            # Applying again does not compound; 0 puts the line's own scale back.
            assert pg.evaluate(f"{O}.setFeatureHeight(-45)") is True
            assert abs(pg.evaluate(f"{O}.opts.manualMmPerPx") - eff) < 1e-9, "a second Apply compounded the correction"
            assert pg.evaluate(f"{O}.setFeatureHeight(0)") is True
            assert abs(pg.evaluate(f"{O}.opts.manualMmPerPx") - base) < 1e-12, "0 did not restore the line's scale"
            # With lens data: the same numbers here (f = 1186), and no hedge.
            pg.evaluate(f"(()=>{{const o={O};o.data.camera={{focal_px:1186,focal_source:'exif'}};}})()")
            assert pg.evaluate(f"{O}.setFeatureHeight(-45)") is True
            text = pg.evaluate("window.__calLastToast.innerText")
            assert "+7.4%" in text and "no lens data" not in text, text
            assert pg.evaluate(f"{O}.featureHeightMM()") == -45
            eff = pg.evaluate(f"{O}.opts.manualMmPerPx")   # f = 1186 exactly now, not 1186.08
            # The offset survives a save and reload (the snapshot carries only the mm/px and
            # the line): it reads back as -45, applying it again does not compound, and 0
            # still finds the line's own scale.
            pg.evaluate(f"""(()=>{{const o={O};const s=o.getRestoreState();
                o.opts.manualMmPerPx=null;o._lineScaleBase=null;o._featureHeightMM=0;o._scaleLine=null;
                o._applyRestore(s);}})()""")
            assert abs(pg.evaluate(f"{O}.opts.manualMmPerPx") - eff) < 1e-9, "the reload lost the corrected scale"
            assert pg.evaluate(f"{O}.featureHeightMM()") == -45, "the reload lost the offset's reading"
            assert pg.evaluate(f"{O}.setFeatureHeight(-45)") is True
            assert abs(pg.evaluate(f"{O}.opts.manualMmPerPx") - eff) < 1e-9, "re-applying after a reload compounded"
            assert pg.evaluate(f"{O}.setFeatureHeight(0)") is True
            assert abs(pg.evaluate(f"{O}.opts.manualMmPerPx") - base) < 1e-12, "0 after a reload missed the line's scale"
            # A new line drawn over an offset is in the ruler's plane: no stale offset reported.
            pg.evaluate(f"{O}.setFeatureHeight(-45)")
            pg.evaluate(f"{O}.setManualScaleFromPixels(581.4, 300, [[600,409.3],[600,990.7]])")
            assert pg.evaluate(f"{O}.featureHeightMM()") == 0, "a new line kept the old offset"
            assert abs(pg.evaluate(f"{O}.opts.manualMmPerPx") - base) < 1e-12
            # Square up over an offset squares from the line's own scale, says the offset is
            # off, and Undo brings the offset back with its scale.
            pg.evaluate(f"{O}.setFeatureHeight(-45)")
            reply["body"] = _result(other="ruler", ruler_found=True)
            pg.evaluate(f"(()=>{{const o={O};o.ann.selectedId={oid};}})()")
            assert pg.evaluate(f"{O}.squareUp()"), "square up over an offset did not apply"
            text = pg.evaluate("window.__calLastToast.innerText")
            assert "-45 mm plane offset is off" in text, text
            assert abs(pg.evaluate(f"{O}.opts.manualMmPerPx") - base) < 1e-12, "squared up from the offset scale"
            assert pg.evaluate(f"{O}.featureHeightMM()") == 0
            pg.locator(".cal-toast-action", has_text="Undo").click()
            pg.wait_for_function(f"!{O}.opts.manualHomography", timeout=5000)
            assert abs(pg.evaluate(f"{O}.opts.manualMmPerPx") - eff) < 1e-9 and pg.evaluate(f"{O}.featureHeightMM()") == -45, \
                "Undo did not bring the offset back"
            pg.evaluate(f"{O}.setFeatureHeight(0)")
            # A squared-up scale is still refused, as before.
            reply["body"] = _result()
            pg.evaluate(f"(()=>{{const o={O};o.ann.selectedId={oid};}})()")
            assert pg.evaluate(f"{O}.squareUp()"), "square up did not apply"
            assert pg.evaluate(f"{O}.setFeatureHeight(-45)") is False, "a squared-up scale accepted a plane offset"
            assert pg.evaluate(f"{O}.featureHeightMM()") == 0

            assert not errors, f"page errors: {errors}"
            browser.close()
        print("E2E_RESULT: PASS (no-ruler message with reason, Redraw scale line + disabled Use the ruler; partial "
              "message; refusal offers Redraw; no-selection text; line scale 0.516 mm/px, 45 mm below -> +7.4% "
              "with the no-lens hedge, 0 restores, survives a reload, new line clears it, Square up says it "
              "dropped it and Undo brings it back, squared still refused)")
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
