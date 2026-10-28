"""Browser end-to-end for the guided photo -> part flow on a phone, as a ruler user meets it.

Boots the app at 390x844 in simple mode, uploads a synthetic part photo (no target: the
scale comes from a ruler line) and walks the steps, with /api/trace stubbed so each reply is
the case under test:

  * the set-scale status names numbered marks, not the ruler's ends;
  * a scale set from the auto-opened chooser brings the simple card back, with its Next;
  * a failed tap trace offers "▭ Draw a box": the box traces as soon as it is let go (two
    requests in all, the second inside the box), with no Outline / Auto-trace / tap again;
  * a trace's warnings reach the screen ("check it: …", with Straighten) and stay on the
    outline; a trace the tracer calls background is said as such and leaves the tool armed;
  * the card comes back after the trace, without the Detail bar; the photo fits above the
    card and the zoom buttons are clear of it;
  * after a reload the landing card offers Build and the Top photo, and the job panel from
    the landing names no button that is not there;
  * the job panel marks the photo's current name, moves its 3D view with a role name, and
    refuses when that view is taken;
  * the Detail bar is one docked row on a phone, touches passing through its background;
  * on a touch phone with the part's views set, an unscaled photo's top bar still fits.

Run directly: .venv/bin/python tests/test_flow_fixes_e2e.py. Skips cleanly (exit 0) where
Playwright / a browser isn't available, unless CAMSCAN_E2E_REQUIRE=1.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PORT = os.getenv("CAMSCAN_E2E_PORT", "8213")


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
except Exception as e:
    _skip(f"missing dependency: {e}")

_CHROMIUM = None
for cand in (os.getenv("PLAYWRIGHT_CHROMIUM"), "/opt/pw-browsers/chromium"):
    if cand and os.path.exists(cand):
        _CHROMIUM = cand
        break

OV = "document.querySelector('.cal-view').__overlay"
CHOOSER = '.cal-coach-card[aria-label="Set the scale"]'
SLAB = [[220, 230], [580, 230], [580, 370], [220, 370]]      # the part, in photo px (posted 1:1)


def _photo():
    img = np.full((600, 800, 3), 195, np.uint8)
    cv2.rectangle(img, (220, 230), (580, 370), (55, 55, 55), -1)
    path = os.path.join(tempfile.gettempdir(), "flow-fixes-e2e-part.jpg")
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


def _screen(pg, x, y):
    """A photo pixel's position on screen."""
    return pg.evaluate(f"""(([x,y])=>{{const o={OV}, c=o.canvas.getBoundingClientRect();
        const p=o.vp.imageToCanvas(x,y);
        return [c.left+p[0]*c.width/o.canvas.width, c.top+p[1]*c.height/o.canvas.height];}})""", [x, y])


def _simple(pg):
    return pg.evaluate("document.body.classList.contains('camscan-simple')")


def _toast(pg):
    return pg.evaluate("(()=>{const t=window.__calLastToast;return t&&t.parentNode?t.innerText:'';})()")


def run(browser, errors, dialogs):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && window.CalibPart3D && document.querySelector('.landing-card')", timeout=15000)
    pg.evaluate("localStorage.setItem('camscan.simplemode','1');localStorage.setItem('calib.coachSeen','1')")
    pg.reload(wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)

    # /api/trace answers from a queue; every request is kept.
    replies, sent = [], []

    def on_trace(route):
        sent.append(json.loads(route.request.post_data or "{}"))
        body = replies.pop(0) if replies else {"ok": False, "error": "no_outline", "hint": "nothing queued"}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    pg.route("**/api/trace", on_trace)

    pg.set_input_files("#uploader input[type=file]", _photo())
    pg.wait_for_function("(()=>{const v=document.querySelector('.cal-view');return v&&v.__overlay&&"
                         "v.__overlay.img&&v.__overlay.img.naturalWidth>0;})()", timeout=20000)
    pg.wait_for_selector(CHOOSER, state="visible", timeout=8000)
    assert _simple(pg), "upload did not start in simple mode"

    # 1. Ruler: the status names numbered marks; once the scale is set, the card is back.
    pg.locator("#cal-scale-ruler").click()
    pg.wait_for_function(f"{OV}.opts.mode==='setscale'", timeout=5000)
    hint = pg.evaluate(f"{OV}._toolHint()")
    assert "two numbered marks far apart" in hint and "one end" not in hint, f"set-scale status: {hint!r}"
    assert not _simple(pg), "the ruler did not leave simple mode"
    time.sleep(0.7)                                          # a poll sees the step under way
    assert not _simple(pg), "simple mode came back before the scale was set"
    pg.evaluate(f"(()=>{{const o={OV};o.setManualScaleFromPixels(360,180,[[220,400],[580,400]]);o.setMode('select');}})()")
    pg.wait_for_function("document.body.classList.contains('camscan-simple')", timeout=3000)
    pg.wait_for_function("(()=>{const c=document.getElementById('cal-simple');return c&&c.innerText.includes('Next: ✨ Trace outline');})()",
                         timeout=3000)
    card = pg.inner_text("#cal-simple")
    assert "box round the part, then tap it" not in card, f"card still promises a box first: {card!r}"
    # The photo fits above the card, and the zoom buttons are clear of it.
    pg.wait_for_function(f"""(()=>{{const o={OV}, c=o.canvas.getBoundingClientRect();
        const b=c.top+o.vp.imageToCanvas(0,o.img.height)[1]*c.height/o.canvas.height;
        return b<=document.getElementById('cal-simple').getBoundingClientRect().top+1;}})()""", timeout=3000)
    zoom = pg.evaluate("""[...document.querySelectorAll('.cal-zoom button')].map(b=>{const r=b.getBoundingClientRect();
        const e=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return (e===b||b.contains(e))?'ok':(e&&(e.id||e.className));})""")
    assert zoom and all(z == "ok" for z in zoom), f"zoom buttons under the card: {zoom}"

    # 2. Trace from the card; the tap fails; "Draw a box"; the box traces by itself.
    replies.append({"ok": False, "error": "no_outline",
                    "hint": "Couldn't separate the part from the background here. Draw a box round the part (▭ Area), then tap the part again."})
    replies.append({"ok": True, "points": SLAB, "holes": [],
                    "warnings": ["The bottom side stands out 18 px near the right end — shadow?"]})
    pg.click("#cal-simple-trace")
    pg.wait_for_function(f"{OV}._autoOutlineArm===true", timeout=3000)
    x, y = _screen(pg, 400, 300)
    pg.mouse.click(x, y)
    box_btn = pg.locator(".cal-toast-action", has_text="Draw a box")
    box_btn.wait_for(state="visible", timeout=5000)
    box_btn.click()
    pg.wait_for_function(f"{OV}.opts.mode==='rectangle' && {OV}._traceAfterBox===true", timeout=3000)
    (x0, y0), (x1, y1) = _screen(pg, 200, 210), _screen(pg, 600, 390)
    pg.mouse.move(x0, y0)
    pg.mouse.down()
    for k in range(1, 9):
        pg.mouse.move(x0 + (x1 - x0) * k / 8, y0 + (y1 - y0) * k / 8)
    pg.mouse.up()
    pg.wait_for_function(f"{OV}.ann.items.some(a=>a.type==='polyline'&&a.closed)", timeout=8000)
    assert len(sent) == 2, f"{len(sent)} trace requests (want 2: the tap, then the box)"
    roi = sent[1].get("roi")
    assert roi and abs(roi[0] - 200) < 3 and abs(roi[2] - 400) < 4, f"second trace not inside the box: roi {roi}"
    shown = _toast(pg)
    assert "check it" in shown and "stands out 18 px" in shown and "Straighten" in shown, f"warning not shown: {shown!r}"
    kept = pg.evaluate(f"{OV}.ann.items.filter(a=>a.type==='polyline'&&a.closed).map(a=>a.traceWarnings||[])")
    assert kept and kept[0] and "stands out" in kept[0][0], f"warning not kept on the outline: {kept}"
    assert not pg.evaluate(f"{OV}.ann.items.some(a=>a.type==='rectangle')"), "the box was not used up by the trace"

    # 3. The card comes back once the outline is there, and the Detail bar goes with it.
    pg.wait_for_function("document.body.classList.contains('camscan-simple') && !document.getElementById('cal-trace-detail')",
                         timeout=3000)

    # 4. A trace the tracer calls background: said as such, tool still armed.
    replies.append({"ok": True, "points": SLAB, "holes": [], "hint": "That traced the background — tap ✖ Not the part."})
    pg.evaluate(f"{OV}.autoOutline([400,300])")
    pg.wait_for_function("(()=>{const t=window.__calLastToast;return t&&t.parentNode&&t.innerText.includes('traced the background');})()",
                         timeout=5000)
    shown = _toast(pg)
    assert "Outline traced" not in shown, f"background trace reported as a success: {shown!r}"
    armed = pg.evaluate(f"[{OV}.opts.mode, {OV}._autoOutlineArm]")
    assert armed == ["outline", True], f"tool not left armed after a background trace: {armed}"
    pg.evaluate(f"{OV}.setMode('select')")

    # 5. Make the outline the Top view (simple card chip), then reload.
    pg.evaluate(f"(()=>{{const o={OV};o.ann.selectedId=o.ann.items.find(a=>a.type==='polyline'&&a.closed).id;o.requestDraw();}})()")
    pg.wait_for_function("document.body.classList.contains('camscan-simple')", timeout=3000)
    pg.locator('#cal-simple [data-role="top"]').first.click()
    pg.wait_for_function("!!window.CalibJob.part().views.top", timeout=5000)
    pg.evaluate("window.CalibJob.flush()")
    pg.reload(wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && window.CalibPart3D && document.querySelector('#cal-landing-job')", timeout=15000)
    pg.wait_for_selector("#cal-landing-build", timeout=5000)
    landing = pg.inner_text("#cal-landing-job")
    assert "Continue: Build 3D part" in landing and "Open the Top photo" in landing, f"landing card: {landing!r}"
    pg.click("#cal-landing-build")
    pg.wait_for_selector("#cal-part-card", timeout=5000)
    assert "Top" in pg.inner_text("#cal-part-card"), "Build from the landing card did not open the part"
    pg.reload(wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && window.CalibPart3D && document.querySelector('#cal-landing-open')", timeout=15000)
    pg.evaluate("window.CalibJob.open()")
    pg.wait_for_selector("#cal-job-panel", timeout=5000)
    panel = pg.inner_text("#cal-job-panel")
    assert "⬅" not in panel and "No photo is open" in panel, f"panel from the landing: {panel[:300]!r}"
    pg.click("#cal-job-panel button[aria-label='Close']")
    pg.click("#cal-landing-open")
    pg.wait_for_function("(()=>{const v=document.querySelector('.cal-view');return v&&v.__overlay&&v.__overlay.img&&"
                         "v.__overlay.img.naturalWidth>0;})()", timeout=15000)

    # 6. Job panel: the current name is marked; Side moves the 3D view; a taken view refuses.
    pg.evaluate("window.CalibJob.open()")
    pg.wait_for_selector("#cal-job-panel", timeout=5000)
    on = pg.evaluate("[...document.querySelectorAll('#cal-job-panel button[aria-pressed=\"true\"]')].map(b=>b.innerText.trim())")
    assert on == ["✓ Top"], f"current name not marked: {on}"
    pg.locator("#cal-job-panel button", has_text="Side").first.click()
    pg.wait_for_function("(()=>{const v=window.CalibJob.part().views;return !!v.side && !v.top;})()", timeout=5000)
    on = pg.evaluate("[...document.querySelectorAll('#cal-job-panel button[aria-pressed=\"true\"]')].map(b=>b.innerText.trim())")
    assert on == ["✓ Side"], f"panel not redrawn with the new name: {on}"
    labels = pg.evaluate("(async()=>{await window.CalibJob.flush();return (await window.CalibJob.persisted()).views.map(v=>v.label);})()")
    assert labels == ["Side"], f"stored names: {labels}"
    pg.evaluate("(()=>{const p=window.CalibJob.part();p.views.top=Object.assign({},p.views.side,{source:'another-photo'});"
                "window.CalibJob.savePart();})()")
    pg.evaluate("window.CalibJob.open()")
    pg.wait_for_selector("#cal-job-panel", timeout=5000)
    pg.locator("#cal-job-panel button", has_text="Top").first.click()
    pg.wait_for_function("(()=>{const s=document.getElementById('cal-job-status');return s&&s.innerText.includes('already set');})()",
                         timeout=3000)
    views = pg.evaluate("(()=>{const v=window.CalibJob.part().views;return [!!v.side, v.top&&v.top.source];})()")
    assert views == [True, "another-photo"], f"a refused rename changed the views: {views}"
    pg.click("#cal-job-panel button[aria-label='Close']")

    # 7. On a phone the Detail bar is one docked row; its background lets touches through.
    assert not _simple(pg), "a reopened side should be in the full toolbar"
    replies.append({"ok": True, "points": SLAB, "holes": []})
    pg.evaluate(f"{OV}.autoOutline([400,300])")
    pg.wait_for_selector("#cal-trace-detail", timeout=5000)
    bar = pg.evaluate("""(()=>{const b=document.getElementById('cal-trace-detail'),r=b.getBoundingClientRect();
        return {docked:b.getAttribute('data-docked'), h:r.height, l:r.left, r:r.right,
                pe:getComputedStyle(b).pointerEvents,
                done:getComputedStyle([...b.querySelectorAll('button')].pop()).pointerEvents};})()""")
    assert bar["docked"] == "1" and bar["h"] <= 60 and bar["l"] <= 10 and bar["r"] >= 380, f"Detail bar not docked: {bar}"
    assert bar["pe"] == "none" and bar["done"] == "auto", f"Detail bar takes touches off the dots: {bar}"
    ctx.close()
    return len(sent)


def phone_topbar(browser, errors, dialogs):
    """A touch phone (44 px buttons) with the part's views set and the photo unscaled: the
    scale chip used to grow to "Set scale" and push ⛶ past the right edge (354-398 of 390)."""
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                              is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && window.CalibPart3D && document.querySelector('.landing-card')", timeout=15000)
    pg.evaluate("localStorage.setItem('camscan.simplemode','0');localStorage.setItem('calib.coachSeen','1')")
    pg.reload(wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
    pg.set_input_files("#uploader input[type=file]", _photo())
    pg.wait_for_function("(()=>{const v=document.querySelector('.cal-view');return v&&v.__overlay&&"
                         "v.__overlay.img&&v.__overlay.img.naturalWidth>0&&document.getElementById('cal-tb-scale');})()",
                         timeout=20000)
    pg.wait_for_selector(CHOOSER, state="visible", timeout=8000)
    pg.locator(".cal-coach").click(position={"x": 4, "y": 4})
    pg.wait_for_selector(CHOOSER, state="detached", timeout=5000)
    pg.evaluate(f"(()=>{{const o={OV};o.setManualScaleFromPixels(360,180,[[220,400],[580,400]]);"
                "window.CalibAnn.addPolyline(o.ann,[[220,230],[580,230],[580,370],[220,370]],o.getScale(),o.opts.units,null,true);"
                "o.ann.selectedId=o.ann.items[o.ann.items.length-1].id;o.requestDraw();"
                "const p=o.partProfile();['top','side','end'].forEach(r=>window.CalibJob.setPartView(r,p));window.CalibJob.captureCurrent('Top',true);"
                "o.clearManualScale();o.requestDraw();})()")
    pg.wait_for_function("(()=>{const b=document.getElementById('cal-tb-part');return b&&b.offsetParent&&"
                         "document.getElementById('cal-tb-scale').classList.contains('cal-chip-uncal');})()", timeout=5000)
    un = pg.evaluate("""(()=>{const t=document.querySelector('.cal-topbar');
        const kids=[...t.querySelectorAll('button')].filter(b=>b.offsetParent);
        return {sw:document.documentElement.scrollWidth, iw:innerWidth,
                chip:document.getElementById('cal-tb-scale').innerText.trim().replace(/\\s+/g,' '),
                off:kids.filter(b=>b.getBoundingClientRect().right>innerWidth+0.5)
                        .map(b=>(b.innerText.trim()||b.title)+'@'+Math.round(b.getBoundingClientRect().right)),
                all:kids.map(b=>Math.round(b.getBoundingClientRect().left)+'-'+Math.round(b.getBoundingClientRect().right))};})()""")
    assert un["sw"] <= un["iw"] and not un["off"], f"unscaled photo: top bar runs off the screen: {un}"
    ctx.close()
    return un["chip"]


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
            n = run(browser, errors, dialogs)
            chip = phone_topbar(browser, errors, dialogs)
            assert not errors, f"page errors: {errors}"
            assert not dialogs, f"unexpected dialogs: {dialogs}"
            browser.close()
        print(f"E2E_RESULT: PASS (ruler status names marks; card back after scale and trace; failed tap -> Draw a box -> "
              f"traced in the box ({n} trace requests in all); warnings and background hint shown; landing offers Build "
              f"and the Top photo; job panel moves/refuses the view; Detail bar docked; unscaled chip {chip!r} fits)")
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
