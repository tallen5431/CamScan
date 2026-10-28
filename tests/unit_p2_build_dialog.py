"""The Build 3D part dialog's rules, run in node without a browser.

calib.part3d.js decides, before any server call, whether an outline is the wrong view for
the role it is being saved as, how many pieces a part needs on the user's print bed, which
two views a refused build is about, and how a build's result is put into words. Those are
plain functions (CalibPart3D._rules); this drives them with the sizes from the step-pad
cover that prompted them:

  * a 679 x 164.6 mm Top: 3 pieces on a 256 mm bed, cut near 226 / 453 mm;
  * an oblique 160 x 133.6 mm outline saved as Side against that Top: warned, and pointed
    at the End role it fits;
  * the server's "End is 12% wider than Top" refusal: read back into {view, axis, sizes}
    whether or not the server sends it as data;
  * a 3 mm cover downloaded plate-down with the box unticked: never "upside down";
  * a cover whose deck barely touches the bed: never "ready to print";
  * a fit warning said once, Match buttons that say what else they move, and none for a view
    a typed size pins; a photo scale change after a Match clears the Match.

    python tests/unit_p2_build_dialog.py

Needs node (as the smoke suite's client checks do); without it every check is skipped.
"""
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")


SCRIPT = r"""
require(process.argv[1]);                       // tests/support/dom_stub.js
window.CalibJob = { part: () => ({ views: { top: { size_mm: [679, 164.6] }, end: { size_mm: [175.7, 80] } }, opts: {} }) };
require(process.argv[2]);                       // assets/calib.part3d.js
const R = window.CalibPart3D._rules;
const top = { size_mm: [679, 164.6] };
const out = {};
out.bed679 = R.bedLine([679, 164.6, 79.7]);
out.bedSmall = R.bedLine([200, 90, 25]);
out.bedDiag = R.fitsBed(300, 20, [256, 256]);          // only on the diagonal
out.bedWide = R.bedLine([679, 300, 50]);
localStorage.setItem('camscan.printBed', JSON.stringify([350, 350]));
out.bed350 = R.bedLine([679, 164.6, 79.7]);
localStorage.removeItem('camscan.printBed');
out.warnSide = R.assignWarning('side', { size_mm: [133.6, 160.0] }, { top: top });
out.warnOk = R.assignWarning('side', { size_mm: [680, 80] }, { top: top });
out.warnTurned = R.assignWarning('end', { size_mm: [80, 165] }, { top: top, side: { size_mm: [679, 80] } });
out.warnSame = R.assignWarning('end', { size_mm: [679, 166] }, { top: top });
out.sizeTurned = R.viewSize({ size_mm: [90, 200], rot: 0 }, 'top');
out.sizeScaled = R.viewSize({ size_mm: [100, 50], scale: 0.9 }, 'end');
out.missingTop = R.missingNote({ top: top });
out.missingAll = R.missingNote({ top: top, end: { size_mm: [165, 80] } });
out.mmText = R.mismatchOf({ ok: false, error: "The end view's width is 12% longer than the top view's (177.8 vs 158.7 mm). One of those photos is scaled wrong — check its reference." });
out.mmDetail = R.mismatchOf({ ok: false, error: 'x', detail: { view: 'side', axis: 'height', have_mm: 50, want_mm: 45, setter: 'end' } });
out.mmNone = R.mismatchOf({ ok: false, error: 'Give the part\'s height (depth), or add a view that shows it.' });
out.shellOld = R.describe({ size_mm: [200, 90, 25], kind: 'shell', upside_down: true,
                            note: 'Hollow, 3.0 mm walls, open underneath; turned plate-down for printing.' });
out.shellNew = R.describe({ size_mm: [200, 90, 25], kind: 'shell', upside_down: false, plate_down: true,
                            orientation: 'plate-down, open side up — ready to print (no supports)' });
out.solidFlip = R.describe({ size_mm: [200, 90, 25], kind: 'solid', upside_down: true });
out.fits = R.describe({ size_mm: [679, 164.6, 79.7], deskew: [{ view: 'top', deg: 1.1 }],
                        fits: [{ view: 'end', axis: 'width', pct: 6.7, have_mm: 175.7, want_mm: 164.6, setter: 'top', kind: 'rescale' }] });
out.oldFits = R.describe({ size_mm: [200, 90, 25], fits: [{ view: 'end', axis: 'width', pct: 3.2 }] });
// A cover whose deck is not on the bed: from an older server (says "ready to print") and the
// current one (says it is not).
const bedW = 'Only 2% of the deck lies on the bed — the top edge of the End trace is curved or sloped, so the print may come loose.';
out.bedOld = R.describe({ size_mm: [679, 155, 50], kind: 'shell', wall_mm: 3, plate_down: true, warnings: [bedW],
                          orientation: 'plate-down, open side up — ready to print (no supports)',
                          note: 'Hollow, 3.0 mm walls, open underneath; downloads plate-down, open side up — ready to print (no supports).' });
out.bedNew = R.describe({ size_mm: [679, 155, 50], kind: 'shell', wall_mm: 3, plate_down: true, warnings: [bedW], bed_contact: 0.02,
                          orientation: 'plate-down, open side up — but the deck is not flat on the bed, not ready to print' });
out.bedFallback = R.describe({ size_mm: [679, 155, 50], kind: 'shell', upside_down: true, warnings: [bedW] });
// A fit over 3% comes with a linked warning: one line, not two.
const fw = "The end view's width is 7.7% off the top view's (170.7 vs 158.5 mm); the whole end view was rescaled to match, so its other size moved with it. Check that photo's reference.";
out.fitOnce = R.describe({ size_mm: [700, 158.5, 47], warnings: [fw, 'Something else.'],
                           fits: [{ view: 'end', axis: 'width', pct: 7.66, have_mm: 170.65, want_mm: 158.5, setter: 'top', kind: 'rescale', warning: fw }] });
// A typed length the ends trim, and the ends said once.
out.trimmed = R.describe({ size_mm: [698.7, 160.2, 51.2], ends: { slope_deg: 30, radius_mm: 0 },
                           note: 'Ends: 30° slope (typed — there is no Side view).',
                           fits: [{ view: 'top', axis: 'length', pct: -3.0, have_mm: 678.8, want_mm: 700, setter: 'known', kind: 'rescale', final_mm: 698.74 }] });
out.parts = R.describeParts({ size_mm: [679, 164.6, 79.7], warnings: ['A warning.'] });
// The raised-face note: a cover whose Top was scaled from a ruler line with no offset.
window.CalibJob = { part: () => ({ views: { top: { size_mm: [679, 164.6], ruler: true, plane_mm: 0 } }, opts: {} }) };
out.raised = R.describe({ size_mm: [679, 164.6, 45], kind: 'shell', wall_mm: 3 });
out.raisedSolid = R.describe({ size_mm: [679, 164.6, 45], kind: 'solid' });
window.CalibJob = { part: () => ({ views: { top: { size_mm: [679, 164.6], ruler: true, plane_mm: -45 } }, opts: {} }) };
out.raisedSet = R.describe({ size_mm: [679, 164.6, 45], kind: 'shell', wall_mm: 3 });
// Match buttons: both ways, each saying what else moves; only one when a typed size pins the setter.
window.CalibJob = { part: () => ({ views: { top: { size_mm: [620, 155] }, end: { size_mm: [155, 60] } }, opts: {} }) };
out.matchBoth = R.matchButtons({ view: 'end', axis: 'width', have_mm: 175, want_mm: 165, setter: 'top' }, { top: [700, 165], end: [175, 50] });
out.matchPinned = R.matchButtons({ view: 'end', axis: 'width', have_mm: 155, want_mm: 175, setter: 'known', setter_pinned: true, setter_view: 'top' });
out.measured = R.measuredLine({ deskew: [{ view: 'end', deg: -9.92 }], measured_mm: { end: [185.17, 59.18] } });
out.needOne = R.needsDepth({ top: { size_mm: [700, 175] } }, {});
out.needTyped = R.needsDepth({ top: { size_mm: [700, 175] } }, { depth: 50 });
out.needKnown = R.needsDepth({ top: { size_mm: [700, 175] } }, { known: { height: 50 } });
out.needTwo = R.needsDepth({ top: { size_mm: [700, 175] }, end: { size_mm: [175, 50] } }, {});
// sync(): the photo's own scale changed under a Match factor (a raised-face offset): the
// factor goes, from every view on that photo, and a toast says so. A moved dot keeps it.
const toasts = [];
const prof = { points: [[0, 0], [100, 0], [100, 30]], mm_per_px: 0.5172, image_height: 1000, size_mm: [100, 30] };
const ov = { ann: { items: [{ id: 7, type: 'polyline', closed: true }] }, isCalibrated: () => true,
             partProfile: () => prof, opts: { manualMmPerPx: 0.5172 }, featureHeightMM: () => 0,
             _toast: (m) => { toasts.push(m); return {}; } };
document.querySelector = (q) => q === '.cal-view' ? { __overlay: ov } : null;
const job = { p: { views: { top: { ann_id: 7, source: 'ph1', points: prof.points, mm_per_px: 0.5172, image_height: 1000,
                                   scale: 1.10564, scale_undo: 1, scale_note: 'matched to End width' },
                            cavity: { ann_id: 99, source: 'ph1', scale: 1.10564, scale_undo: 1 } }, opts: {} },
              part() { return this.p; }, photoId: () => 'ph1', savePart() {} };
window.CalibJob = job;
prof.points = [[0, 0], [101, 0], [100, 30]];                 // a dragged dot: same mm/px
window.CalibPart3D.sync();
out.syncDot = { scale: job.p.views.top.scale, toasts: toasts.length };
prof.mm_per_px = 0.5552;                                     // the offset: 7.35% on the mm/px
window.CalibPart3D.sync();
out.syncScale = { top: job.p.views.top.scale === undefined, cavity: job.p.views.cavity.scale === undefined,
                  undo: job.p.views.top.scale_undo === undefined, toast: toasts[0] || '' };
console.log(JSON.stringify(out));
"""


def main():
    node = shutil.which("node")
    if node is None:
        print("[PASS] build dialog rules — skipped, no node")
        print("UNIT_RESULT: PASS")
        return 0
    r = subprocess.run([node, "-e", SCRIPT, str(ROOT / "tests" / "support" / "dom_stub.js"),
                        str(ROOT / "assets" / "calib.part3d.js")],
                       capture_output=True, text=True, timeout=60)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        check("calib.part3d.js loads in node", False, (r.stderr or r.stdout)[-400:])
        print("UNIT_RESULT: FAIL")
        return 1
    check("calib.part3d.js loads in node", True, "rules exposed")

    b = o["bed679"]
    check("679 mm part on a 256 mm bed: 3 pieces, cuts near 226 / 453",
          "256 mm bed" in b and "3 pieces" in b and "226 / 453" in b, b)
    check("a 200 mm part fits a 256 mm bed: no bed line", o["bedSmall"] == "", repr(o["bedSmall"]))
    check("a 300 x 20 mm part fits a 256 mm bed on the diagonal", o["bedDiag"] is True, str(o["bedDiag"]))
    check("a part wider than the bed says so", "Wider than your 256 mm bed" in o["bedWide"], o["bedWide"])
    check("the bed setting is read from localStorage (350 mm: 2 pieces)",
          "350 mm bed" in o["bed350"] and "2 pieces" in o["bed350"], o["bed350"])

    w = o["warnSide"] or ""
    check("160 x 134 outline as Side against a 679 Top is warned",
          "length (160 mm) is 76% shorter than the Top view’s (679 mm)" in w, w)
    check("...and pointed at the role it fits", "fits as the End view" in w, w)
    check("an outline that agrees is not warned", o["warnOk"] is None, repr(o["warnOk"]))
    check("an outline that matches turned a quarter says so",
          "turned a quarter" in (o["warnTurned"] or ""), repr(o["warnTurned"]))
    check("an outline the size of the Top, saved as End, is called a top view",
          "looks like a top view" in (o["warnSame"] or ""), repr(o["warnSame"]))
    check("...with the article the role takes", "not an end view" in (o["warnSame"] or ""), repr(o["warnSame"]))
    check("a tall Top outline is laid along its length, as the server does",
          o["sizeTurned"] == [200, 90], str(o["sizeTurned"]))
    check("a matched view's sizes include its scale", o["sizeScaled"] == [90, 45], str(o["sizeScaled"]))
    check("a lone Top asks for a Side or End photo, or a typed height",
          "Add a Side or End photo for the height, or type the height" in o["missingTop"],
          o["missingTop"])
    check("Top + End needs nothing more", o["missingAll"] == "Every size has a view.", o["missingAll"])

    m = o["mmText"] or {}
    check("a refusal's text is read back into which views disagree",
          m.get("view") == "end" and m.get("axis") == "width" and m.get("setter") == "top"
          and abs(m.get("have_mm", 0) - 177.8) < 1e-9 and abs(m.get("want_mm", 0) - 158.7) < 1e-9, str(m))
    check("the server's own detail wins when sent", (o["mmDetail"] or {}).get("view") == "side", str(o["mmDetail"]))
    check("a refusal about something else offers no match", o["mmNone"] is None, str(o["mmNone"]))

    for key in ("shellOld", "shellNew"):
        t = o[key]
        check(f"a cover downloaded plate-down never says upside down ({key})",
              "upside down" not in t.lower() and "plate-down" in t, t)
    check("a solid turned over by the user still says so",
          "Downloads upside down for printing." in o["solidFlip"], o["solidFlip"])
    f = o["fits"]
    check("a fit shows both readings and who set it",
          "Width: Top 164.6 mm, End 175.7 mm (6.7% apart) — using Top" in f and 'data-use-axis="width"' in f
          and 'data-role="end"' in f and ">Use End’s width<" in f
          and 'title="Use End’s width: Top is stretched to 175.7 mm in width, its length stays as measured"' in f, f)
    check("a deskew is reported", "Top view turned 1.1° to square it." in f, f)
    check("a fit from a server without readings keeps the old wording",
          "The end view was stretched 3.2% in width to match." in o["oldFits"], o["oldFits"])

    for key in ("bedOld", "bedNew", "bedFallback"):
        t = o[key]
        check(f"a deck that is not on the bed is never called ready to print ({key})",
              "of the deck lies on the bed" in t and "ready to print" not in t.replace("not ready to print", ""), t)
    t = o["fitOnce"]
    check("a fit over 3% is said once, on its fit line, with the advice",
          t.count("7.7%") == 1 and "Check that photo’s reference." in t and "Something else." in t, t)
    t = o["trimmed"]
    check("a typed length the ends trim says so on its fit line",
          "using your number; the views trim it to 698.7 mm" in t, t)
    check("the typed ends are said once", t.count("Ends:") == 1, t)
    pp = o["parts"]
    check("the result splits into the Part summary and the notes",
          len(pp["summary"]) == 1 and pp["summary"][0].startswith("Part:") and any("A warning." in n for n in pp["notes"])
          and any("pieces" in n for n in pp["notes"]), str(pp))
    check("a cover whose Top came from a ruler line, no offset, gets the raised-face note with its height",
          "Measuring a raised face → −45 mm" in o["raised"], o["raised"])
    check("...not on a solid part, nor once an offset is set",
          "raised face" not in o["raisedSolid"] and "raised face" not in o["raisedSet"],
          o["raisedSolid"] + " | " + o["raisedSet"])
    mb = o["matchBoth"]
    check("Match buttons each say what else they move",
          "Scale End to match Top ×0.943 — its height too, 50 → 47 mm" in mb
          and "Scale Top to match End ×1.061 — its length too, 700 → 742 mm" in mb and "Measuring a raised face" in mb, mb)
    mp = o["matchPinned"]
    check("no 'Scale Top' button when a typed size pins the Top; a note says why",
          mp.count("data-match=") == 1 and 'data-match="end"' in mp and "Scale End to match your width" in mp
          and "Top is set by the size you typed" in mp, mp)
    check("a refusal shows a turned view's measured size",
          "End as measured: 185.2 × 59.2 mm, after turning it 9.9°" in o["measured"], o["measured"])
    check("one view, nothing typed: no preview request (needsDepth); typed depth, known height or two views: request",
          o["needOne"] == "height" and o["needTyped"] is None and o["needKnown"] is None and o["needTwo"] is None,
          f'{o["needOne"]} {o["needTyped"]} {o["needKnown"]} {o["needTwo"]}')
    check("a moved dot keeps the Match factor", o["syncDot"] == {"scale": 1.10564, "toasts": 0}, str(o["syncDot"]))
    ss = o["syncScale"]
    check("a photo scale change under a Match clears it from every view on that photo, with a toast",
          ss["top"] and ss["cavity"] and ss["undo"] and "Match ×1.106 was removed" in ss["toast"], str(ss))

    ok = all(results)
    print("UNIT_RESULT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
