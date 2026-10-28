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
  * a 3 mm cover downloaded plate-down with the box unticked: never "upside down".

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
          and 'data-role="end"' in f and ">Use End<" in f, f)
    check("a deskew is reported", "Top view turned 1.1° to square it." in f, f)
    check("a fit from a server without readings keeps the old wording",
          "The end view was stretched 3.2% in width to match." in o["oldFits"], o["oldFits"])

    ok = all(results)
    print("UNIT_RESULT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
