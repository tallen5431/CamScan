"""Browser end-to-end test for the toolbar, the first screens and the Set-scale chooser.

Each check is a layout or routing fault that cost a step on the way from a few photos to a
3D part, and none of them shows up anywhere but a real browser at a real screen size:

  1. 📷 Add photo (was "New") keeps the photo: upload, scale, trace, assign Top, press it —
     after the reload the part holds that photo (CalibJob.count()==1) and the landing no
     longer says "no photos saved". It used to keep the Top view and drop its photo. A
     photo the user already added as "Front" keeps that name (and is recaptured with the
     marks made since), rather than being renamed "View 1" and dropping off the checklist.
  2. Desktop rail (1400x900): the column overflowed at BOTH ends and its top, Select, was
     clipped where it could not be scrolled to. It must start at or below the rail's top.
  3. Set-scale chooser: the ruler comes first, every button is a 44 px touch target (Apply,
     Verify, Clear and Close were ~19 px tall), and "card" is gone from the raised-face box.
  4. Phone (390x844): a selected outline's buttons are ONE row at most 56 px tall, Tidy and
     Use in 3D part are really tappable there (hit-tested), and Delete / Not the part /
     Extrude sit behind ⋯ More. With three views assigned the top bar stays one row, the
     🧩 chip an accented icon with a badge.
  5. First run in simple mode: one "no scale" prompt, not the first-run coach stacked on
     the simple card; and the chooser's ruler option leaves simple mode, so its card no
     longer swallows the two taps on the ruler.

Not part of the fast smoke test (it needs Chromium + a running server). Run it directly:

    .venv/bin/python tests/test_layout_e2e.py

It SKIPS cleanly (exit 0) where Playwright or a browser isn't available, so it never breaks
an environment that can't run a browser — unless CAMSCAN_E2E_REQUIRE=1, as CI sets it.
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
PORT = os.getenv("CAMSCAN_E2E_PORT", "8206")


def _skip(msg):
    # A skip is a pass by default; CI sets CAMSCAN_E2E_REQUIRE=1, where a skip is a failure.
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


def _photo():
    """A dark slab on a light floor, no calibration target: the scale comes from a line."""
    img = np.full((600, 800, 3), 195, np.uint8)
    cv2.rectangle(img, (220, 230), (580, 370), (55, 55, 55), -1)
    path = os.path.join(tempfile.gettempdir(), "layout-e2e-part.jpg")
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


def _page(browser, w, h, simple, coach_seen, errors, dialogs):
    ctx = browser.new_context(viewport={"width": w, "height": h})
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    pg.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
    pg.wait_for_function("window.CalibJob && window.CalibPart3D && document.querySelector('.landing-card')",
                         timeout=15000)
    if simple is not None or coach_seen:
        pg.evaluate("([s, c]) => { if (s !== null) localStorage.setItem('camscan.simplemode', s);"
                    " if (c) localStorage.setItem('calib.coachSeen', '1'); }",
                    [None if simple is None else ("1" if simple else "0"), coach_seen])
        pg.reload(wait_until="networkidle")
        pg.wait_for_function("window.CalibJob && document.querySelector('.landing-card')", timeout=15000)
    return ctx, pg


def _upload(pg, path):
    pg.set_input_files("#uploader input[type=file]", path)
    pg.wait_for_function("(()=>{const v=document.querySelector('.cal-view');"
                         "return v&&v.__overlay&&v.__overlay.img&&v.__overlay.img.naturalWidth>0"
                         "&&document.getElementById('cal-tb-scale');})()", timeout=20000)


def _close_chooser(pg):
    """The unscaled photo opens the chooser by itself; tap its backdrop to close it."""
    pg.wait_for_selector(CHOOSER, state="visible", timeout=8000)
    pg.locator(".cal-coach").click(position={"x": 4, "y": 4})
    pg.wait_for_selector(CHOOSER, state="detached", timeout=5000)


def _scale_and_trace(pg):
    # 360 px across the slab is 180 mm: a ruler line, as the user's photos are scaled.
    pg.evaluate(f"{OV}.setManualScaleFromPixels(360, 180, [[220,400],[580,400]])")
    pg.evaluate(f"(()=>{{const o={OV};o.setMode('outline');o.autoOutline([400,300]);}})()")
    pg.wait_for_function(f"{OV}.ann.items.some(a=>a.type==='polyline'&&a.closed)", timeout=20000)
    pg.evaluate(f"(()=>{{const o={OV};const p=o.ann.items.find(a=>a.type==='polyline'&&a.closed);"
                "o.ann.selectedId=p.id;o.setMode('select');o.requestDraw();})()")
    pg.locator("#cal-ctx-3d").wait_for(state="visible", timeout=5000)


def _hit(pg, sel):
    """Does a tap at the centre of `sel` land on it (not on something drawn over it)?"""
    return pg.evaluate("""(sel)=>{const b=document.querySelector(sel);if(!b)return 'missing';
        const r=b.getBoundingClientRect();if(!r.width)return 'hidden';
        const e=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
        return (e===b||b.contains(e))?'ok':(e?(e.id||e.className||e.tagName):'nothing');}""", sel)


def check_new_keeps_photo(browser, errors, dialogs):
    ctx, pg = _page(browser, 1280, 900, False, True, errors, dialogs)
    _upload(pg, _photo())
    _close_chooser(pg)
    _scale_and_trace(pg)
    pg.locator("#cal-ctx-3d").click()
    pg.locator('#cal-part-pick [data-role="top"]').click()
    pg.wait_for_function("!!window.CalibJob.part().views.top", timeout=5000)
    assert pg.evaluate("window.CalibJob.count()") == 0, "photo stored before Add photo was pressed"
    btn = pg.locator("#cal-tb-new")
    assert "Add photo" in btn.inner_text(), f"button label: {btn.inner_text()!r}"
    with pg.expect_navigation(timeout=20000):
        btn.click()
        pg.wait_for_function("document.body.innerText.includes('saved to the part as \"Top\"')", timeout=5000)
    pg.wait_for_function("window.CalibJob && window.CalibJob.ready", timeout=15000)
    pg.evaluate("window.CalibJob.ready")
    n = pg.evaluate("window.CalibJob.count()")
    assert n == 1, f"photo not kept across Add photo: count={n}"
    pg.wait_for_selector("#cal-landing-job", timeout=8000)
    note = pg.inner_text("#cal-landing-job")
    assert "no photos saved" not in note and "1 photo" in note, f"landing note: {note!r}"
    assert not dialogs, f"Add photo asked when the photo was kept: {dialogs}"
    ctx.close()
    return note.replace("\n", " ")


STORED_VIEWS = ("(async()=>{await window.CalibJob.flush();const j=await window.CalibJob.persisted();"
                "return j?j.views.map(v=>({label:v.label,at:v.capturedAt})):[];})()")


def check_add_photo_keeps_label(browser, errors, dialogs):
    ctx, pg = _page(browser, 1280, 900, False, True, errors, dialogs)
    _upload(pg, _photo())
    _close_chooser(pg)
    pg.evaluate(f"{OV}.setManualScaleFromPixels(360, 180, [[220,400],[580,400]])")
    pg.evaluate("window.CalibJob.addView('Front')")
    before = pg.evaluate(STORED_VIEWS)
    assert [v["label"] for v in before] == ["Front"], f"setup: {before}"
    # A mark made after the photo was added must not be lost by Add photo either.
    pg.evaluate(f"(()=>{{const o={OV};window.CalibAnn.addPolyline(o.ann,[[200,200],[400,200],[400,400]],"
                "o.getScale()||0.5,o.opts.units,null,false);o.requestDraw();})()")
    pg.wait_for_timeout(20)
    with pg.expect_navigation(timeout=20000):
        pg.locator("#cal-tb-new").click()
        pg.wait_for_function("document.body.innerText.includes('saved to the part as \"Front\"')", timeout=5000)
    pg.wait_for_function("window.CalibJob && window.CalibJob.ready", timeout=15000)
    pg.evaluate("window.CalibJob.ready")
    after = pg.evaluate(STORED_VIEWS)
    assert [v["label"] for v in after] == ["Front"], f"Add photo relabelled the stored photo: {after}"
    assert after[0]["at"] > before[0]["at"], f"Add photo did not recapture the later marks: {before} -> {after}"
    assert not dialogs, f"Add photo asked when the photo was kept: {dialogs}"
    ctx.close()


def check_desktop(browser, errors, dialogs):
    ctx, pg = _page(browser, 1400, 900, False, True, errors, dialogs)
    _upload(pg, _photo())
    _close_chooser(pg)
    rail = pg.evaluate("""(()=>{const r=document.querySelector('.cal-rail').getBoundingClientRect();
        const s=[...document.querySelectorAll('.cal-rail .cal-tool')].find(b=>b.innerText.includes('Select'))
          .getBoundingClientRect();return {rail:r.top, sel:s.top};})()""")
    assert rail["sel"] >= rail["rail"] - 0.5, f"Select clipped above the rail: {rail}"

    pg.click("#cal-tb-scale")
    pg.wait_for_selector(CHOOSER, state="visible", timeout=5000)
    first = pg.evaluate(f"document.querySelector('{CHOOSER} button').innerText")
    assert "Ruler or tape on the part" in first, f"chooser does not lead with the ruler: {first!r}"
    sizes = pg.evaluate(f"""[...document.querySelectorAll('{CHOOSER} button')]
        .map(b=>({{t:b.innerText.trim().slice(0,24), h:b.getBoundingClientRect().height}}))""")
    small = [s for s in sizes if s["h"] < 44 - 0.5]
    assert not small, f"chooser buttons under 44 px: {small}"
    txt = pg.inner_text(CHOOSER)
    assert "card" not in txt.lower(), f"chooser still says card: {txt!r}"
    assert "below the" in txt and "reference" in txt, "raised-face box lacks the negative-value note"
    ctx.close()
    return len(sizes)


def check_phone(browser, errors, dialogs):
    ctx, pg = _page(browser, 390, 844, False, True, errors, dialogs)
    _upload(pg, _photo())
    _close_chooser(pg)
    _scale_and_trace(pg)
    bar = pg.evaluate("""(()=>{const s=[...document.querySelectorAll('.cal-context button.show')]
        .filter(b=>getComputedStyle(b).display!=='none');
        const t=Math.min(...s.map(b=>b.getBoundingClientRect().top));
        const bt=Math.max(...s.map(b=>b.getBoundingClientRect().bottom));
        return {h:bt-t, names:s.map(b=>b.innerText.trim())};})()""")
    assert bar["h"] <= 56, f"context bar {bar['h']:.0f} px tall: {bar['names']}"
    for sel in ("#cal-ctx-tidy", "#cal-ctx-3d", "#cal-ctx-more"):
        got = _hit(pg, sel)
        assert got == "ok", f"{sel} is not tappable: a tap lands on {got}"
    # ...and none of them runs off the end of the row, where only a sideways scroll finds it.
    clipped = pg.evaluate("""(()=>{const c=document.querySelector('.cal-context').getBoundingClientRect();
        return [...document.querySelectorAll('.cal-context button.show')].filter(b=>getComputedStyle(b).display!=='none')
          .filter(b=>{const r=b.getBoundingClientRect();return r.right>c.right+0.5||r.left<c.left-0.5;})
          .map(b=>b.innerText.trim());})()""")
    assert not clipped, f"context buttons cut off at the row's end: {clipped}"
    assert not any("Delete" in n for n in bar["names"]), f"Delete not folded: {bar['names']}"
    pg.click("#cal-ctx-more")
    more = pg.inner_text("#cal-more-menu")
    assert "Delete" in more and "Not the part" in more and "Extrude" in more, f"More menu: {more!r}"
    pg.locator("#cal-more-menu button", has_text="Extrude").click()
    pg.wait_for_selector("#cal-depth-input", timeout=5000)
    assert "How thick is the part" in pg.inner_text("#cal-depth-input")
    pg.keyboard.press("Escape")

    # Three views assigned: the top bar still fits one row.
    pg.evaluate(f"(()=>{{const p={OV}.partProfile();"
                "['top','side','end'].forEach(r=>window.CalibJob.setPartView(r,p));})()")
    pg.wait_for_function("(()=>{const b=document.getElementById('cal-tb-part');"
                         "return b&&b.offsetParent&&b.querySelector('.cal-badge').innerText==='3';})()", timeout=5000)
    top = pg.evaluate("""(()=>{const t=document.querySelector('.cal-topbar');
        const kids=[...t.querySelectorAll('.cal-icon')].filter(b=>b.offsetParent);
        const tops=kids.map(b=>Math.round(b.getBoundingClientRect().top));
        return {h:t.getBoundingClientRect().height, spread:Math.max(...tops)-Math.min(...tops),
                sw:document.documentElement.scrollWidth,
                right:Math.max(...kids.map(b=>b.getBoundingClientRect().right)),
                primary:document.getElementById('cal-tb-part').classList.contains('cal-btn-primary')};})()""")
    assert top["spread"] <= 4 and top["h"] < 72, f"top bar wrapped: {top}"
    assert top["right"] <= 390 + 0.5 and top["sw"] <= 390, f"top bar runs off the screen: {top}"
    assert top["primary"], "Build 3D part chip is not the accented primary"
    ctx.close()
    return bar["h"], top["h"]


def check_first_run_simple(browser, errors, dialogs):
    ctx, pg = _page(browser, 1280, 900, None, False, errors, dialogs)     # a brand-new visitor
    _upload(pg, _photo())
    pg.wait_for_selector(CHOOSER, state="visible", timeout=8000)
    time.sleep(0.8)            # anything else that would stack has had its chance to appear
    shown = pg.evaluate("""(()=>({coaches:[...document.querySelectorAll('.cal-coach')].filter(c=>c.offsetParent||
        getComputedStyle(c).position==='fixed').length,
        gettingStarted:!!document.querySelector('.cal-coach-card[aria-label="Getting started"]'),
        simple:document.body.classList.contains('camscan-simple')}))()""")
    assert shown["simple"], "a first run does not start in simple mode"
    assert not shown["gettingStarted"] and shown["coaches"] == 1, f"stacked first-run prompts: {shown}"
    # The ruler option leaves simple mode: the simple card no longer sits on the photo.
    pg.locator("#cal-scale-ruler").click()
    pg.wait_for_function(f"{OV}.opts.mode==='setscale' && !document.body.classList.contains('camscan-simple')",
                         timeout=5000)
    pg.wait_for_function("(()=>{const c=document.getElementById('cal-simple');"
                         "return !c||getComputedStyle(c).display==='none';})()", timeout=3000)
    lands = pg.evaluate("""(()=>{const c=document.querySelector('.cal-view canvas').getBoundingClientRect();
        const e=document.elementFromPoint(c.left+c.width/2, c.bottom-40);return e&&e.tagName;})()""")
    assert lands == "CANVAS", f"a tap low on the photo lands on {lands}, not the canvas"
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
            note = check_new_keeps_photo(browser, errors, dialogs)
            check_add_photo_keeps_label(browser, errors, dialogs)
            nbtn = check_desktop(browser, errors, dialogs)
            bar_h, top_h = check_phone(browser, errors, dialogs)
            check_first_run_simple(browser, errors, dialogs)
            assert not errors, f"page errors: {errors}"
            assert not dialogs, f"unexpected dialogs: {dialogs}"
            browser.close()
        print(f"E2E_RESULT: PASS (Add photo kept the Top photo, landing: {note!r}; a 'Front' photo kept its name; Select inside the rail at "
              f"1400x900; {nbtn} chooser buttons >=44 px, ruler first; phone context row {bar_h:.0f} px, Tidy/3D/More "
              f"tappable, top bar one row {top_h:.0f} px with 3 views; first run in simple mode shows one prompt)")
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
