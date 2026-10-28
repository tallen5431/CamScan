"""Unit checks for keeping the photo on screen in the job (assets/calib.job.js).

Drives the real calib.job.js in node, against the DOM stub plus a stand-in overlay, so the
rules behind CalibJob.captureCurrent, setPartView and removeView are checked without a
browser (tests/test_job_persist_e2e.py drives the same paths in Chromium):

  - capturing a photo twice stores it once, and its photoId() — which every 3D view taken
    from it points at — is the same before and after;
  - a photo with nothing done to it is not stored unless asked (a label tap);
  - the label of a stored photo follows its 3D role while it is a default one, and a label
    the user typed is kept;
  - re-assigning a role from the same photo keeps its rotate/flip/covers, and from another
    photo resets them;
  - removing a photo used by the part asks first (cancel keeps it), then flags the view;
  - a photo removed while on screen is not stored again by a reload or an edit, only by a
    label tap, which also clears its view's "photo removed" flag;
  - the guidance names the 3D views (Top / Side / End) and puts the ruler on the face.

Plain python; needs node. Prints one [PASS]/[FAIL] line per check and UNIT_RESULT.
"""
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")


SCRIPT = r"""
require(process.argv[1]);                                   // DOM stub
const mk = () => document.createElement('div');
document.head = mk();
// One .cal-view holding a fresh upload, and an overlay that can be given a scale or marks.
const attrs = { 'data-json': '/uploads/top-1234.calibration.json', 'data-img': 'data:image/jpeg;base64,AAAA' };
const ov = { img: { src: 'data:image/jpeg;base64,AAAA' }, ann: { items: [] }, opts: {},
  isCalibrated(){ return !!this.opts.manualMmPerPx; },
  getViewRecord(label){ return { label, image: 'annotated', units: 'mm', measurements: [],
    scale: { calibrated: this.isCalibrated() }, restore: { raw: 'raw', ann: this.ann.items.slice() } }; } };
const el = { __overlay: ov, getAttribute(k){ return k in attrs ? attrs[k] : null; },
  setAttribute(k, v){ attrs[k] = String(v); }, removeAttribute(k){ delete attrs[k]; } };
document.querySelector = (sel) => sel === '.cal-view' ? el : null;
let confirmAnswer = false, asked = [];
window.confirm = (m) => { asked.push(m); return confirmAnswer; };
require(process.argv[2]);                                   // calib.job.js
const J = window.CalibJob;
(async () => {
  await J.ready;
  const out = {};
  const pid0 = J.photoId();
  out.emptyNotStored = J.captureCurrent() === false && J.count() === 0;
  ov.opts.manualMmPerPx = 0.5;                              // a scale: now there is something to keep
  const a = J.captureCurrent(), b = J.captureCurrent();
  out.twice = { a, b, n: J.count(), same: J.photoId() === pid0, stored: J.isCurrentStored() };
  out.firstLabel = (await J.persisted()).views[0].label;
  out.relabel = (J.captureCurrent('Top'), (await J.persisted()).views[0].label);
  // Roles.
  const prof = { points: [[0,0],[10,0],[10,5]], mm_per_px: 0.5, image_height: 100, size_mm: [10,5], ann_id: 7 };
  J.setPartView('top', prof);
  const v = J.part().views.top; v.rot = 1; v.flip_v = true;
  J.setPartView('side', prof); J.part().views.side.covers = 'start'; J.part().views.side.rot = 3;
  J.setPartView('top', prof); J.setPartView('side', prof);
  out.keep = { rot: J.part().views.top.rot, flip_v: J.part().views.top.flip_v,
               covers: J.part().views.side.covers, srot: J.part().views.side.rot };
  // From another photo the alignment starts square.
  J.part().views.top.source = 'some-other-photo';
  J.setPartView('top', prof);
  out.reset = { rot: J.part().views.top.rot, flip_v: J.part().views.top.flip_v, source: J.part().views.top.source === pid0 };
  // A forced capture of an untouched second photo, under a label.
  attrs['data-json'] = '/uploads/side-5678.calibration.json'; attrs['data-img'] = 'data:image/jpeg;base64,BBBB';
  ov.img = { src: 'data:image/jpeg;base64,BBBB' }; ov.opts = {}; ov.ann = { items: [] };
  out.second = { plain: J.captureCurrent('Side'), forced: J.captureCurrent('Notes', true), n: J.count() };
  J.captureCurrent('End');                                  // "Notes" was typed: it stays
  out.typedKept = (await J.persisted()).views[1].label;
  J.setPartView('end', prof);                               // a view from the second photo
  // Removing the Top photo: cancel keeps it, OK flags its views.
  confirmAnswer = false;
  out.removeCancel = { kept: J.removeView(0) };
  out.afterCancel = { n: J.count(), flag: !!J.part().views.side.photo_removed, asked: asked.slice() };
  confirmAnswer = true;
  out.removeOk = J.removeView(0);
  out.afterOk = { n: J.count(), side: J.part().views.side.photo_removed === true,
                  top: J.part().views.top.photo_removed === true, end: !!J.part().views.end.photo_removed,
                  pts: J.part().views.side.points.length };
  // Removing the photo ON SCREEN (the second one, which holds End): the overlay still has it,
  // with marks and a 3D view, yet neither a reload nor an edit may store it again.
  ov.ann.items.push({ id: 1, type: 'polyline' });
  out.removeOnScreen = J.removeView(0);
  await J.beforeReload();
  out.afterReload = { n: J.count(), flag: J.part().views.end.photo_removed === true,
                      edit: J.captureCurrent(), n2: J.count() };
  // A label tap brings it back, and its End view follows it again.
  out.readd = { ok: J.captureCurrent('End', true), n: J.count(), flag: !!J.part().views.end.photo_removed,
                stored: J.isCurrentStored() };
  console.log(JSON.stringify(out));
  process.exit(0);                                          // the module's timers would keep node alive
})().catch(e => { console.log(JSON.stringify({ error: String(e && e.stack || e) })); process.exit(0); });
"""


def main():
    node = shutil.which("node")
    if node is None:
        check("node available", False, "these checks drive calib.job.js in node")
        print("UNIT_RESULT: FAIL")
        return 1
    r = subprocess.run([node, "-e", SCRIPT, str(ROOT / "tests" / "support" / "dom_stub.js"),
                        str(ROOT / "assets" / "calib.job.js")], capture_output=True, text=True, timeout=60)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        check("calib.job.js runs under the DOM stub", False, (r.stdout + r.stderr)[-600:])
        print("UNIT_RESULT: FAIL")
        return 1
    if "error" in o:
        check("calib.job.js runs under the DOM stub", False, o["error"][-600:])
        print("UNIT_RESULT: FAIL")
        return 1

    check("an untouched photo is not stored", o["emptyNotStored"], f"count stays 0: {o['emptyNotStored']}")
    t = o["twice"]
    check("capturing twice stores the photo once, photoId unchanged",
          t["a"] and t["b"] and t["n"] == 1 and t["same"] and t["stored"], f"{t}")
    check("a photo with no role is stored as 'View N'", o["firstLabel"] == "View 1", o["firstLabel"])
    check("a default label follows the role it is given", o["relabel"] == "Top", o["relabel"])
    k = o["keep"]
    check("re-assigning from the same photo keeps rotate/flip/covers",
          k == {"rot": 1, "flip_v": True, "covers": "start", "srot": 3}, f"{k}")
    rs = o["reset"]
    check("an outline from another photo starts square",
          rs["rot"] == 0 and rs["flip_v"] is False and rs["source"], f"{rs}")
    s = o["second"]
    check("an untouched photo is stored only when asked (label tap)",
          s["plain"] is False and s["forced"] is True and s["n"] == 2, f"{s}")
    check("a label the user typed is not replaced by a role", o["typedKept"] == "Notes", o["typedKept"])
    ac = o["afterCancel"]
    check("removing a photo used by the part asks, and cancel keeps it",
          o["removeCancel"]["kept"] is False and ac["n"] == 2 and not ac["flag"]
          and len(ac["asked"]) == 1 and "Top" in ac["asked"][0] and "Side" in ac["asked"][0], f"{ac}")
    ao = o["afterOk"]
    check("confirming removes the photo and flags only its views",
          o["removeOk"] is True and ao["n"] == 1 and ao["side"] and ao["top"] and not ao["end"]
          and ao["pts"] == 3, f"{ao}")

    ar = o["afterReload"]
    check("a photo removed while on screen stays removed through a reload and an edit",
          o["removeOnScreen"] is True and ar["n"] == 0 and ar["flag"] and ar["edit"] is False
          and ar["n2"] == 0, f"{ar}")
    rd = o["readd"]
    check("a label tap re-adds the removed photo and clears its 'photo removed' flag",
          rd["ok"] is True and rd["n"] == 1 and not rd["flag"] and rd["stored"], f"{rd}")

    src = (ROOT / "assets" / "calib.job.js").read_text(encoding="utf-8")
    rec = src[src.index("var RECOMMENDED"):src.index("var EXTRA_LABELS")]
    labels = [ln.split("label:'")[1].split("'")[0] for ln in rec.splitlines() if "label:'" in ln]
    check("Suggested shots are the 3D views", labels == ["Top", "Side", "End"], f"{labels}")
    check("Suggested shots put the ruler on the face",
          "lay the ruler flat ON the face that photo shows" in src
          and "Keep the same reference (card/marker) flat in every shot" not in src, "footer text")
    check("capture rule #3 credits Square up; #5 has the Area box tip",
          "A ruler line plus Square up corrects tilt along the ruler" in src and "▭ Area box" in src, "rules text")
    simple = (ROOT / "assets" / "calib.simple.js").read_text(encoding="utf-8")
    check("simple card offers Top / Side / End, not Front / Detail",
          "var ROLES=['Top','Side','End'];" in simple and "'Front'" not in simple, "chips")
    check("simple card hint names the real button", "New photo" not in simple and "📷 Add another photo" in simple, "hint")

    ok = all(RESULTS)
    print("UNIT_RESULT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
