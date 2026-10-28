"""Static checks on the toolbar / coach / chooser wording in assets/calib.ui.enhanced.js.

The words are the guidance here: "Depth" invited a running board's ~700 mm length where a
top-traced cover wanted its ~30 mm height, the chooser led with a printed marker nobody had,
and the chip said "manual" to someone holding a ruler. These checks fail if an edit quietly
puts the old words back. Layout and behaviour are checked in tests/test_layout_e2e.py.

    python tests/unit_p7_wording.py
"""
import pathlib
import re
import sys

JS = pathlib.Path(__file__).resolve().parent.parent / "assets" / "calib.ui.enhanced.js"

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")


def main():
    src = JS.read_text(encoding="utf-8")

    present = {
        "coach title": "Photo to 3D part",
        "coach step 1 (ruler)": "along the ruler",
        "coach step 2 (tap, box, Tidy)": "tap the part (if it misses part of it, draw a ▭ box round it first)",
        "coach step 3 (Build)": "then <b>Build 3D part</b>",
        "chooser ruler option": "Ruler or tape on the part",
        "chooser ruler hint": "Tap two numbered marks along its middle",
        "raised face: negative note": "negative value means the outline is below the",
        "Square up subtitle": "Correct camera tilt using the ruler (or the part\\'s sides)",
        "Extrude button": "'⇥ Extrude'",
        "Extrude prompt": "How thick is the part, straight through this outline?",
        "Extrude note": "Traced from above: its height. Ends slope or round? Use 🧩 Use in 3D part.",
        "chip: ruler": "text = 'ruler';",
        "chip: ruler squared": "text = 'ruler ✓ squared';",
        "chip: rectangle": "text = 'rectangle';",
        "Add photo button": "'📷', 'Add photo'",
        "Add photo toast": "This photo was saved to the part as",
        "More menu": "cal-ctx-more",
    }
    for name, s in present.items():
        check(name, s in src, "present" if s in src else f"missing {s!r}")

    gone = {
        "old coach title": "Measure in three steps",
        "old Depth prompt": "How far does this profile run?",
        "old Depth label": "'⇥ Depth'",
        # Trace outline arms a tap; the box is the fallback the failed trace offers.
        "coach: box before the tap": "draw a box round the part, then tap it",
        "old chip 'manual'": "text = 'manual'",
        "old chip 'paper'": "text = 'paper'",
        "old Square up subtitle": "tilt from the two long sides'",
        "old New label": "'⬅️', 'New'",
        "'card' in the raised-face box": "mm above card",
    }
    for name, s in gone.items():
        check(name, s not in src, "gone" if s not in src else f"still has {s!r}")

    # Ruler first, printed marker second, rectangle after.
    i_ruler, i_marker = src.find("Ruler or tape on the part"), src.find("I printed a square")
    i_rect = src.find("A rectangle you know the size of")
    check("ruler, then marker, then rectangle", 0 <= i_ruler < i_marker < i_rect,
          f"ruler@{i_ruler} marker@{i_marker} rectangle@{i_rect}")

    # Chooser button classes carry a 44 px touch target themselves.
    m = re.search(r"\.cal-coach-primary, \.cal-coach-secondary\{([^}]*)\}", src)
    check("coach buttons 44 px", bool(m and "min-height: 44px" in m.group(1)),
          m.group(1).strip()[:60] if m else "rule not found")

    # The desktop rail starts at its top, so overflow only happens where it scrolls.
    m = re.search(r"\.cal-rail\{(\s*flex-flow: column nowrap;[^}]*)\}", src)
    check("desktop rail flex-start", bool(m and "justify-content: flex-start" in m.group(1)),
          "found" if m else "rule not found")

    # The first-run coach steps aside in simple mode.
    m = re.search(r"function maybeShowCoach\(\)\{(.*?)coach = document", src, re.S)
    check("coach skipped in simple mode", bool(m and "camscan-simple" in m.group(1)),
          "guarded" if m else "function not found")

    ruler_wording()
    stale_button_names()

    ok = all(results)
    print("UNIT_RESULT: PASS" if ok else "UNIT_RESULT: FAIL")
    return 0 if ok else 1


def ruler_wording():
    """One ruler instruction everywhere: two numbered marks far apart, down its middle.

    The set-scale status bar and every Square up redraw message used to say "one end of the
    ruler, then the other ... end to end" while the length box said "numbered marks, not the
    physical ends"; a line end to end called "30 cm" read every size 4.7-6.3% small.
    """
    root = JS.parent.parent
    files = [root / "assets" / "calibrationOverlay.js", JS, root / "assets" / "calib.simple.js",
             root / "assets" / "calib.job.js", root / "app.py", root / "README.txt"]
    bad = ("one end of the ruler", "end, then the other", "middle, end to end")
    hits = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            low = line.lower()
            hits += [f"{f.name}:{i}: {b!r}" for b in bad if b in low]
    check("no 'end to end' ruler instruction", not hits, "; ".join(hits) or "none")
    ov = files[0].read_text(encoding="utf-8")
    check("set-scale status names numbered marks", "tap two numbered marks far apart, along the ruler's middle" in ov,
          "present" if "tap two numbered marks far apart" in ov else "missing")


def stale_button_names():
    """A button renamed in one file must not live on in another file's hint text."""
    root = JS.parent.parent
    hits = []
    for f in sorted((root / "assets").glob("*.js")):
        src = f.read_text(encoding="utf-8")
        for old in ("⬅ New", "⬅️ New"):
            if old in src:
                hits.append(f"{f.name}: {old!r}")
    check("no hint names the old '⬅ New' button", not hits, "; ".join(hits) or "none")


if __name__ == "__main__":
    sys.exit(main())
