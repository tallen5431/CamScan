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
        "coach step 2 (box, tap, Tidy)": "draw a box round the part, then tap it",
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
        "old chip 'manual'": "text = 'manual'",
        "old chip 'paper'": "text = 'paper'",
        "old Square up subtitle": "tilt from the two long sides'",
        "old New label": "'⬅️', 'New'",
        "'card' in the raised-face box": "mm above card",
    }
    for name, s in gone.items():
        check(name, s not in src, "gone" if s not in src else f"still has {s!r}")

    # The ruler option is offered before the printed marker.
    i_ruler, i_marker = src.find("Ruler or tape on the part"), src.find("I printed a square")
    check("ruler before marker", 0 <= i_ruler < i_marker, f"ruler@{i_ruler} marker@{i_marker}")

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

    ok = all(results)
    print("UNIT_RESULT: PASS" if ok else "UNIT_RESULT: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
